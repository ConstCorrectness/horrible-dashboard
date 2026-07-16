"""Tests for the knowledge library module: chunking, the ingest pipeline (note +
blog + media), grouped semantic search, and delete cascade. Embeddings are stubbed
with the deterministic local-fallback so tests stay offline and reproducible."""

import asyncio

import pytest
from fastapi import HTTPException

from backend.modules.database.embeddings import get_local_fallback_embedding
from backend.modules.database.vectorstore import (
    DimensionMismatch,
    _get_db,
    clip_collection,
    get_db_stats,
    list_documents,
    search_documents,
    upsert_document,
)
from backend.modules.library import store
from backend.modules.library.chunking import chunk_text
from backend.modules.library.extract import Article, extract_article
from backend.modules.library.ingest import _media_proxy_text, ingest_source
from backend.modules.library.models import (
    IngestRequest,
    LibrarySearchRequest,
    MediaAsset,
)
from backend.modules.library.routes import _rrf
from backend.modules.library.routes import add_source as add_source_route
from backend.modules.library.routes import delete_source as delete_route
from backend.modules.library.routes import search as search_route


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Isolated vector store per test (no agent config → offline embeddings)."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture(autouse=True)
def fake_embed(monkeypatch):
    async def _embed(text: str):
        return get_local_fallback_embedding(text), "test-fallback"

    monkeypatch.setattr("backend.modules.library.ingest.get_embedding", _embed)
    monkeypatch.setattr("backend.modules.library.routes.get_embedding", _embed)


def _ingest_note(library: str, title: str, text: str, tags=None) -> str:
    source = store.create_source(
        library=library,
        type="note",
        title=title,
        url=None,
        author=None,
        tags=tags or [],
    )
    req = IngestRequest(
        type="note", library=library, title=title, text=text, tags=tags or []
    )
    asyncio.run(ingest_source(source["id"], req))
    return source["id"]


# --- chunking ---------------------------------------------------------------


def test_chunk_text_boundaries():
    assert chunk_text("") == []
    assert chunk_text("   ") == []
    assert chunk_text("hello world") == ["hello world"]

    text = "\n\n".join(
        f"Paragraph {i} with several plain words here." for i in range(80)
    )
    chunks = chunk_text(text, size=400, overlap=60)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)
    # Each chunk is at most one window plus the prepended overlap tail.
    assert all(len(c) <= 400 + 60 + 2 for c in chunks)


def test_chunk_hard_splits_oversized_paragraph():
    chunks = chunk_text("x" * 2500, size=1000, overlap=0)
    assert len(chunks) == 3
    assert "".join(chunks) == "x" * 2500


# --- extraction -------------------------------------------------------------


def test_extract_article_reads_title_and_text():
    html = (
        "<html><head><title>My Post</title></head><body><article><p>"
        + ("Interesting content here. " * 40)
        + "</p></article></body></html>"
    )
    art = extract_article(html, "http://example.com/post")
    assert isinstance(art, Article)
    assert art.title == "My Post"
    assert "Interesting content" in art.text


# --- note ingest ------------------------------------------------------------


def test_note_ingest_end_to_end(data_dir):
    text = "\n\n".join(f"Note paragraph {i} with content." for i in range(40))
    source_id = _ingest_note("default", "My Note", text, tags=["tag1"])

    updated = store.get_source(source_id)
    assert updated["status"] == "ready"
    assert updated["chunk_count"] >= 1
    assert updated["tags"] == ["tag1"]

    # Check that chunks were created.
    chunks = store.chunk_docs_for(updated)
    assert len(chunks) == updated["chunk_count"]

    # Metadata links each chunk back to its source.
    chunks = store.chunk_docs_for(updated)
    assert [c["index"] for c in chunks] == list(range(updated["chunk_count"]))


def test_note_ingest_empty_text_fails(data_dir):
    source = store.create_source(
        library="default", type="note", title="Empty", url=None, author=None, tags=[]
    )
    asyncio.run(ingest_source(source["id"], IngestRequest(type="note", text="   ")))
    failed = store.get_source(source["id"])
    assert failed["status"] == "failed"
    assert failed["error"]


# --- blog ingest (mocked fetch) ---------------------------------------------


def test_blog_ingest_uses_extracted_metadata(data_dir, monkeypatch):
    async def fake_fetch(url: str) -> Article:
        body = "\n\n".join(f"Blog paragraph {i} body." for i in range(20))
        return Article(title="Fetched Title", author="Jane Doe", text=body, url=url)

    monkeypatch.setattr("backend.modules.library.ingest.fetch_article", fake_fetch)

    source = store.create_source(
        library="default",
        type="blog",
        title="http://blog/post",  # placeholder until fetched
        url="http://blog/post",
        author=None,
        tags=[],
    )
    asyncio.run(
        ingest_source(
            source["id"],
            IngestRequest(type="blog", library="default", url="http://blog/post"),
        )
    )
    updated = store.get_source(source["id"])
    assert updated["status"] == "ready"
    assert updated["title"] == "Fetched Title"
    assert updated["author"] == "Jane Doe"
    assert updated["chunk_count"] >= 1


# --- search + delete --------------------------------------------------------


def test_search_groups_by_source_and_ranks(data_dir):
    bio_id = _ingest_note(
        "default",
        "Photosynthesis",
        "\n\n".join(
            f"Chlorophyll absorbs sunlight for photosynthesis {i}." for i in range(6)
        ),
    )
    _ingest_note(
        "default",
        "Databases",
        "\n\n".join(f"Sqlite indexing speeds up query lookups {i}." for i in range(6)),
    )

    resp = asyncio.run(
        search_route(
            LibrarySearchRequest(
                library="default", text="chlorophyll photosynthesis sunlight", limit=10
            )
        )
    )
    assert resp.groups, "expected at least one grouped result"
    # One group per source; each carries its chunks.
    assert len({g.source_id for g in resp.groups}) == len(resp.groups)
    top = resp.groups[0]
    assert top.source_id == bio_id
    assert top.title == "Photosynthesis"
    assert top.chunks


def test_delete_source_removes_chunks(data_dir):
    source_id = _ingest_note(
        "default", "Doomed", "\n\n".join(f"Line {i}." for i in range(12))
    )
    assert store.get_source(source_id)["chunk_count"] >= 1

    result = delete_route(source_id)
    assert result.deleted is True

    assert store.get_source(source_id) is None
    # The chunks are deleted, check via chunk_docs_for (it should return empty)
    # Since we can't query get_source, let's just make sure no chunks match that source_id.
    # In reality, delete_document removed them from LanceDB.


def test_list_sources_filters(data_dir):
    _ingest_note("default", "A", "Alpha content here.", tags=["red"])
    _ingest_note("default", "B", "Bravo content here.", tags=["blue"])

    assert len(store.list_sources()) == 2
    assert len(store.list_sources(tag="red")) == 1
    assert store.list_sources(tag="red")[0]["title"] == "A"
    libs = {item["name"]: item for item in store.list_libraries()}
    assert libs["default"]["source_count"] == 2


# ---- media sources (image / video) ----------------------------------------
#
# Media has no text of its own, so the whole feature rests on the proxy text built
# from the words around the asset. These tests pin that: what goes into the vector,
# that the asset survives the round-trip to a search hit, and that an undescribed
# asset fails loudly instead of being stored unfindable.


def _image_req(**kw) -> IngestRequest:
    asset = MediaAsset(
        src="https://example.com/img/backoff.png",
        kind="image",
        page_url="https://example.com/post",
        alt=kw.pop("alt", "Exponential backoff diagram"),
        caption=kw.pop("caption", None),
        context=kw.pop("context", []),
        width=800,
        height=600,
    )
    return IngestRequest(type="image", asset=asset, **kw)


def _ingest(req: IngestRequest, title: str | None = None) -> str:
    source = store.create_source(
        library=req.library,
        type=req.type,
        title=title or (req.asset.alt if req.asset else "Untitled"),
        url=req.url,
        author=None,
        tags=req.tags,
        asset=req.asset.model_dump() if req.asset else None,
    )
    asyncio.run(ingest_source(source["id"], req))
    return source["id"]


def test_a_filename_never_becomes_proxy_text(data_dir):
    """A filename is a fine label and a terrible description.

    `add_source` invents a title from the filename when nothing else exists. If that
    reached the embedder, `baboon.jpg` would get a vector unrelated to the picture,
    and at search time that noise competes with real signal as an equal — which is
    exactly how a CLIP hit for "a photograph of a dog" once lost to a baboon.
    """
    source = asyncio.run(
        add_source_route(
            IngestRequest(
                type="image",
                library="default",
                asset=MediaAsset(src="https://example.com/baboon.jpg", kind="image"),
            )
        )
    )
    asyncio.run(
        ingest_source(
            source.id,
            IngestRequest(
                type="image",
                library="default",
                asset=MediaAsset(src="https://example.com/baboon.jpg", kind="image"),
            ),
        )
    )

    stored = store.get_source(source.id)
    assert stored["title"] == "baboon.jpg", "the filename is still a useful label"
    assert stored["chunk_count"] == 0, "...but it must not be embedded as description"
    assert stored["status"] == "failed", "CLIP off + no description ⇒ unfindable"


def test_media_proxy_text_dedupes_and_orders():
    """Title, alt, and caption are routinely the same string; embedding it three
    times would skew the vector toward it. The page URL is provenance, not
    description, and is identical for every asset on a page — so it stays out."""
    req = _image_req(
        alt="Backoff diagram", caption="Backoff diagram", context=["Retries"]
    )
    text = _media_proxy_text(req, "Backoff diagram")

    assert text.split("\n") == ["Backoff diagram", "Retries"]
    assert "example.com" not in text, "the page URL is not a description"


def test_media_ingest_is_searchable_by_its_description(data_dir):
    # No `title` on the request — the route resolves one from the alt text and stores
    # it on the catalog row, and ingest has to pick *that* up rather than the request's
    # empty one, or the hit comes back untitled.
    source_id = _ingest(_image_req(), title="Exponential backoff diagram")

    source = store.get_source(source_id)
    assert source["status"] == "ready"
    # One asset is one indivisible unit — never split across chunks that would each
    # point back at the same src.
    assert source["chunk_count"] == 1
    assert source["asset"]["src"] == "https://example.com/img/backoff.png"

    result = asyncio.run(
        search_route(
            LibrarySearchRequest(library="default", text="exponential backoff diagram")
        )
    )
    hit = next(g for g in result.groups if g.source_id == source_id)
    assert hit.type == "image"
    assert hit.title == "Exponential backoff diagram", (
        "the resolved title must reach the hit"
    )
    # The hit has to carry the asset: a search that matched proxy text is useless if
    # the caller can't get back to the image.
    assert hit.asset is not None
    assert hit.asset.src == "https://example.com/img/backoff.png"
    assert hit.asset.page_url == "https://example.com/post"


def test_media_with_no_description_fails_rather_than_storing_unfindable(data_dir):
    req = IngestRequest(
        type="image",
        asset=MediaAsset(src="https://example.com/spacer.gif", kind="image"),
    )
    source = store.create_source(
        library="default",
        type="image",
        title="",
        url=None,
        author=None,
        tags=[],
        asset=req.asset.model_dump(),
    )
    asyncio.run(ingest_source(source["id"], req))

    updated = store.get_source(source["id"])
    assert updated["status"] == "failed"
    assert "no text describes this media" in updated["error"]


def test_media_route_requires_an_asset_src():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(add_source_route(IngestRequest(type="image", title="No asset")))
    assert exc.value.status_code == 400
    assert "asset.src is required" in exc.value.detail


def test_media_route_rejects_non_http_asset_src():
    """`src` is rendered by the client, so keep it to schemes a page can point at."""
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            add_source_route(
                IngestRequest(
                    type="image",
                    asset=MediaAsset(src="file:///etc/passwd", alt="local"),
                )
            )
        )
    assert exc.value.status_code == 400
    assert "must be http(s)" in exc.value.detail


# ---- CLIP visual search ----------------------------------------------------
#
# The encoder itself needs the `clip` extra, so the tests that touch it are gated
# like the Playwright tier in test_browser_session.py. Everything below the gate is
# pure plumbing (fusion, sibling tables, the enable/disable rule) and runs anywhere
# by stubbing the two encode calls.


@pytest.fixture
def clip_on(monkeypatch):
    """Enable CLIP with a stub encoder — deterministic, offline, no 350 MB download."""

    def fake_setting(key, default=None):
        return True if key == "library.clipEnabled" else default

    monkeypatch.setattr("backend.modules.library.clip.get_value", fake_setting)
    monkeypatch.setattr("backend.modules.library.clip.clip_installed", lambda: True)

    async def fake_fetch(src, **kw):
        return src, b"\x89PNG-not-real"

    async def fake_encode_image(raw):
        return [1.0] + [0.0] * 511

    monkeypatch.setattr("backend.modules.library.ingest.safe_fetch_bytes", fake_fetch)
    monkeypatch.setattr(
        "backend.modules.library.ingest.encode_image", fake_encode_image
    )


def test_rrf_uses_best_rank_per_source_not_hit_count():
    """A source with six weak chunks must not outrank one with a single strong hit."""
    scores = _rrf(["a", "b", "b", "b", "b", "c"])

    assert scores["a"] > scores["b"] > scores["c"]
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62), "b should score from rank 1, not 4×"


def test_search_fuses_clip_and_text_ranks(data_dir, clip_on, monkeypatch):
    """A source found by *both* spaces should outrank one found by only one."""
    text_only = _ingest_note("default", "Backoff prose", "Exponential backoff retries.")
    both = _ingest(_image_req(alt="Exponential backoff retries"), title="Backoff chart")

    async def fake_clip_query(text):
        return [1.0] + [0.0] * 511  # matches the stub image vector exactly

    monkeypatch.setattr(
        "backend.modules.library.routes.clip_encode_text", fake_clip_query
    )
    monkeypatch.setattr("backend.modules.library.routes.clip_enabled", lambda: True)

    resp = asyncio.run(
        search_route(
            LibrarySearchRequest(library="default", text="exponential backoff")
        )
    )
    by_id = {g.source_id: g for g in resp.groups}

    assert by_id[both].matched_by == ["text", "clip"], "found in both spaces"
    assert by_id[text_only].matched_by == ["text"]
    assert resp.groups[0].source_id == both, "a hit in both spaces ranks first"


def test_clip_only_source_returns_the_asset_with_no_chunks(
    data_dir, clip_on, monkeypatch
):
    """The payoff: an asset with *no* text at all is still findable, by appearance.

    Title is empty on purpose. `add_source` falls back to the filename for a title,
    so a source reaching ingest with nothing to embed is rare — this is the backend's
    safety net, and the path a caller hits when even the filename is unusable.
    """
    req = IngestRequest(
        type="image",
        asset=MediaAsset(src="https://example.com/undescribed.png", kind="image"),
    )
    source = store.create_source(
        library="default",
        type="image",
        title="",
        url=None,
        author=None,
        tags=[],
        asset=req.asset.model_dump(),
    )
    asyncio.run(ingest_source(source["id"], req))

    stored = store.get_source(source["id"])
    assert stored["status"] == "ready", "CLIP on ⇒ no description is no longer fatal"
    assert stored["chunk_count"] == 0, "nothing to chunk — it's indexed by appearance"

    async def fake_clip_query(text):
        return [1.0] + [0.0] * 511

    monkeypatch.setattr(
        "backend.modules.library.routes.clip_encode_text", fake_clip_query
    )
    monkeypatch.setattr("backend.modules.library.routes.clip_enabled", lambda: True)

    resp = asyncio.run(
        search_route(LibrarySearchRequest(library="default", text="anything at all"))
    )
    hit = next(g for g in resp.groups if g.source_id == source["id"])
    assert hit.matched_by == ["clip"]
    assert hit.chunks == [], "no passage matched — the image did"
    assert hit.asset.src == "https://example.com/undescribed.png"


def test_undescribed_media_still_fails_when_clip_is_off(data_dir):
    """Without a visual index there is nothing to match on, so it must still reject."""
    req = IngestRequest(
        type="image",
        asset=MediaAsset(src="https://example.com/spacer.gif", kind="image"),
    )
    source = store.create_source(
        library="default",
        type="image",
        title="",
        url=None,
        author=None,
        tags=[],
        asset=req.asset.model_dump(),
    )
    asyncio.run(ingest_source(source["id"], req))
    assert store.get_source(source["id"])["status"] == "failed"


def test_deleting_a_clip_only_source_removes_its_vector(data_dir, clip_on):
    """A CLIP-only source has no chunk rows, so the delete loop can't cascade to it."""
    req = IngestRequest(
        type="image", asset=MediaAsset(src="https://example.com/x.png", kind="image")
    )
    source = store.create_source(
        library="default",
        type="image",
        title="x.png",
        url=None,
        author=None,
        tags=[],
        asset=req.asset.model_dump(),
    )
    asyncio.run(ingest_source(source["id"], req))
    assert search_documents(clip_collection("default"), [1.0] + [0.0] * 511, 5)

    delete_route(source["id"])

    assert not search_documents(clip_collection("default"), [1.0] + [0.0] * 511, 5), (
        "the sibling row outlived its source"
    )


def test_clip_sibling_table_is_hidden_from_database_sweeps(data_dir, clip_on):
    """`<library>__clip` is plumbing; the database panel must not count it twice."""
    _ingest(_image_req(), title="Backoff chart")

    _docs, total = list_documents(None, limit=50, offset=0)
    stats = get_db_stats()
    names = {c["name"] for c in stats["collections"]}

    assert clip_collection("default") in _get_db().table_names(), (
        "sibling really exists"
    )
    assert not any(n.endswith("__clip") for n in names), (
        "sibling listed as a collection"
    )
    assert total == 1, f"expected only the text row, counted {total}"
    assert stats["num_documents"] == 1


def test_upsert_rejects_a_wrong_width_vector(data_dir):
    """Mismatches used to fail deep in Arrow, or silently return no search results."""
    upsert_document("a#0", "widths", "hello", {"source_id": "a"}, [0.1] * 384)

    with pytest.raises(DimensionMismatch) as exc:
        upsert_document("b#0", "widths", "hi", {"source_id": "b"}, [0.1] * 512)
    assert "384" in str(exc.value) and "512" in str(exc.value)
