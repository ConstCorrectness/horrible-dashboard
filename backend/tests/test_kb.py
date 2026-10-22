"""Knowledge base: catalog store, bookmark unification, index modes, retrieval
across libraries, and the HTTP surface (read back through the response models —
a field missing from `response_model` is silently dropped, so it's tested there)."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.database.embeddings import get_local_fallback_embedding
from backend.modules.database.vectorstore import upsert_documents
from backend.modules.kb import profiles, service, store
from backend.modules.kb.models import SearchRequest
from backend.modules.library import store as library_store


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _vec(text: str) -> list[float]:
    return get_local_fallback_embedding(text)


@pytest.fixture
def fake_query_embed(monkeypatch):
    """Queries embed with the same hash as the stored rows, under a real-looking
    model name — so ranking is deterministic and the fallback guard stays quiet."""

    async def _embed(text: str, **_kw):
        return _vec(text), "test/embed"

    monkeypatch.setattr("backend.modules.database.embeddings.get_embedding", _embed)
    return _embed


# --- store ---------------------------------------------------------------------


def test_upsert_by_url_is_idempotent_on_canonical_form():
    item, created = store.upsert_by_url("https://www.Example.com/post/?utm_source=x")
    assert created
    again, created2 = store.upsert_by_url("http://example.com/post", title="Real")
    assert not created2
    assert again["id"] == item["id"]
    assert again["title"] == "Real"
    with pytest.raises(sqlite3.IntegrityError):
        store.create_item(url="https://example.com/post")


def test_update_never_clobbers_unsent_fields_and_tracks_completion():
    item = store.create_item(url="https://a.test/x", notes="mine", status="reading")
    store.upsert_by_url("https://a.test/x", title="Crawled title")
    row = store.get_item(item["id"])
    assert row["notes"] == "mine" and row["status"] == "reading"
    done = store.update_item(item["id"], status="done")
    assert done["completed_at"]
    reopened = store.touch_opened(item["id"])
    assert reopened["status"] == "done"  # opening a finished item doesn't un-finish it
    back = store.update_item(item["id"], status="reading")
    assert back["completed_at"] is None


def test_collections_filter_and_scope_include_descendants():
    course = store.create_item(
        url="https://c.test/course", kind="course", collections=["ml"]
    )
    lesson = store.create_item(
        url="https://c.test/course/l1", kind="lesson", parent_id=course["id"]
    )
    store.create_item(url="https://c.test/other", collections=["web"])
    rows, total = store.list_items(collection="ml")
    assert total == 1 and rows[0]["id"] == course["id"]
    assert rows[0]["collections"] == ["ml"]
    assert lesson["id"] in store.collection_item_ids(["ml"])
    top, _ = store.list_items(top_level=True)
    assert lesson["id"] not in {r["id"] for r in top}


def test_tag_filter_matches_whole_tags_only():
    store.create_item(url="https://t.test/1", tags=["ml"])
    store.create_item(url="https://t.test/2", tags=["mlops"])
    rows, _ = store.list_items(tag="ml")
    assert [r["url"] for r in rows] == ["https://t.test/1"]


def test_delete_cascades_to_children():
    course = store.create_item(url="https://d.test/c", kind="course")
    store.create_item(url="https://d.test/c/1", kind="lesson", parent_id=course["id"])
    doomed = store.delete_item(course["id"])
    assert len(doomed) == 2
    assert store.get_by_url("https://d.test/c/1") is None


def test_browser_bookmarks_migrate_once_with_ids_kept(tmp_path):
    from backend.modules.browser import store as browser_store

    browser_store.init_browser_db()
    with browser_store.get_db_conn() as conn:
        conn.execute(
            "INSERT INTO browser_bookmarks (id, url, title, tags) VALUES "
            "('legacy1', 'https://old.test/', 'Old', '[\"x\"]')"
        )
    store._initialized.clear()
    migrated = store.get_item("legacy1")
    assert migrated is not None
    assert migrated["starred"] and migrated["kind"] == "bookmark"
    assert migrated["tags"] == ["x"]
    # A second init must not re-import (or resurrect a deleted one).
    store.delete_item("legacy1")
    store._initialized.clear()
    store.init_kb_db()
    assert store.get_item("legacy1") is None


def test_unstarring_an_invested_item_keeps_it(client):
    made = client.post(
        "/api/browser/bookmarks", json={"url": "https://keep.test/", "title": "K"}
    ).json()
    store.update_item(made["id"], notes="important")
    assert client.delete(f"/api/browser/bookmarks/{made['id']}").json()["ok"]
    kept = store.get_item(made["id"])
    assert kept is not None and not kept["starred"]
    assert client.get("/api/browser/bookmarks").json()["bookmarks"] == []


# --- progress / tree -----------------------------------------------------------


def test_course_progress_counts_leaf_lessons():
    course = store.create_item(url="https://p.test/c", kind="course")
    ch = store.create_item(
        url="https://p.test/c/1", kind="chapter", parent_id=course["id"]
    )
    for i in range(3):
        lesson = store.create_item(
            url=f"https://p.test/c/1/{i}", kind="lesson", parent_id=ch["id"], ordinal=i
        )
        if i < 2:
            store.update_item(lesson["id"], status="done")
    item = service.item_model(course["id"])
    assert item.progress.done == 2 and item.progress.total == 3
    assert item.child_count == 1
    node = service.tree(course["id"])
    assert [n.item.url for n in node.children[0].children] == [
        f"https://p.test/c/1/{i}" for i in range(3)
    ]


# --- index modes ---------------------------------------------------------------


def test_text_mode_files_a_strict_structured_source(monkeypatch):
    queued: list[dict] = []
    monkeypatch.setattr(
        "backend.modules.library.routes.enqueue_task",
        lambda task_type, payload: queued.append(payload),
    )
    item = store.create_item(url="https://aman.ai/primers/ai/RAG/", title="RAG")
    asyncio.run(service.set_index_mode(item["id"], "text"))
    row = store.get_item(item["id"])
    assert row["index_mode"] == "text" and row["library"] == service.PAGES_LIBRARY
    req = queued[0]["req"]
    assert req["structured"] and req["strict_embeddings"]
    # The aman profile's selector rides along for a matching URL.
    assert req["content_selector"] == "article.post-content"
    assert service.item_model(item["id"]).index_status == "queued"


def test_back_to_link_drops_indexed_text(monkeypatch):
    monkeypatch.setattr(
        "backend.modules.library.routes.enqueue_task", lambda **_k: None
    )
    item = store.create_item(url="https://drop.test/a")
    asyncio.run(service.set_index_mode(item["id"], "text"))
    source_id = store.get_item(item["id"])["source_id"]
    upsert_documents(
        service.PAGES_LIBRARY,
        [(f"{source_id}#0", "t", {"source_id": source_id}, _vec("t"))],
    )
    asyncio.run(service.set_index_mode(item["id"], "link"))
    assert library_store.get_source(source_id) is None
    assert store.get_item(item["id"])["source_id"] is None


def test_site_mode_is_refused_on_a_single_item():
    item = store.create_item(url="https://s.test/")
    with pytest.raises(ValueError):
        asyncio.run(service.set_index_mode(item["id"], "site"))


# --- retrieval -----------------------------------------------------------------


def _indexed(url: str, library: str, chunks: list[tuple[str, str]], **fields) -> dict:
    """An item with a ready library source holding `chunks` (section, text)."""
    item = store.create_item(url=url, **fields)
    source = library_store.create_source(
        library=library, type="blog", title=url, url=url, author=None, tags=[]
    )
    library_store.set_status(source["id"], "ready", chunk_count=len(chunks))
    rows = [
        (
            f"{source['id']}#{i}",
            text,
            {
                "source_id": source["id"],
                "chunk_index": i,
                "section": section,
                "anchor": section.lower().replace(" ", "-"),
                "embed_model": "test/embed",
                "title": url,
            },
            _vec(text),
        )
        for i, (section, text) in enumerate(chunks)
    ]
    upsert_documents(library, rows)
    return store.update_item(item["id"], source_id=source["id"], library=library)


def test_search_merges_libraries_and_cites_sections(fake_query_embed):
    _indexed(
        "https://aman.ai/primers/ai/RAG/",
        "kb-aman.ai",
        [("Fusion", "reciprocal rank fusion merges rankings"), ("Intro", "rag intro")],
    )
    _indexed(
        "https://apxml.com/courses/x/c/l",
        "kb-apxml.com",
        [("Cosine Similarity", "cosine similarity of vectors")],
    )
    res = asyncio.run(
        service.search(SearchRequest(query="reciprocal rank fusion merges rankings"))
    )
    assert set(res.libraries) == {"kb-aman.ai", "kb-apxml.com"}
    top = res.passages[0]
    assert top.section == "Fusion"
    assert top.link == "https://aman.ai/primers/ai/RAG/#fusion"
    assert not res.fused_by_rank


def test_search_scoped_to_a_collection(fake_query_embed):
    _indexed(
        "https://in.test/a", "kb-pages", [("A", "gradient descent")], collections=["ml"]
    )
    _indexed("https://out.test/b", "kb-pages", [("B", "gradient descent")])
    res = asyncio.run(
        service.search(SearchRequest(query="gradient descent", collections=["ml"]))
    )
    assert [p.url for p in res.passages] == ["https://in.test/a"]


def test_search_refuses_a_fallback_query_vector(monkeypatch):
    _indexed("https://f.test/a", "kb-pages", [("A", "text")])

    async def _fallback(text: str, **_kw):
        return _vec(text), "local-fallback"

    monkeypatch.setattr("backend.modules.database.embeddings.get_embedding", _fallback)
    with pytest.raises(service.SearchUnavailable):
        asyncio.run(service.search(SearchRequest(query="text")))


def test_search_ignores_rows_no_item_owns(fake_query_embed):
    _indexed("https://own.test/a", "kb-pages", [("A", "owned text")])
    upsert_documents(
        "kb-pages",
        [("orphan#0", "owned text", {"source_id": "orphan"}, _vec("owned text"))],
    )
    res = asyncio.run(service.search(SearchRequest(query="owned text")))
    assert all(p.source_id != "orphan" for p in res.passages)


# --- profiles ------------------------------------------------------------------


def test_builtin_profiles_validate():
    for profile in profiles.list_profiles():
        if profile["builtin"]:
            profiles.validate(profile)


def test_parameterised_profile_resolves_from_a_matching_url():
    hit = profiles.match(
        "https://apxml.com/courses/vector-databases-semantic-search/chapter-1/x"
    )
    assert hit is not None
    profile, params = hit
    assert profile["id"] == "apxml-course"
    assert params == {"course": "vector-databases-semantic-search"}
    spec = profiles.resolve(profile, params)
    assert spec["start_urls"] == [
        "https://apxml.com/courses/vector-databases-semantic-search"
    ]
    # The placeholder is filled (escaped) and the {0,2} quantifier is left alone.
    assert spec["allow_patterns"] == [
        "^/courses/vector\\-databases\\-semantic\\-search(/[^/]+){0,2}/?$"
    ]
    assert spec["path_root"] == "/courses/vector-databases-semantic-search"
    with pytest.raises(ValueError, match="needs course"):
        profiles.resolve(profile, {})


def test_roadmap_is_not_a_course():
    hit = profiles.match("https://apxml.com/courses/roadmap")
    assert hit is None or hit[0]["id"] != "apxml-course"


def test_user_profile_round_trip_and_builtin_ids_protected():
    spec = {
        "id": "my-blog",
        "start_urls": ["https://blog.test/"],
        "allow_domains": ["blog.test"],
        "allow_patterns": ["^/posts/"],
        "content_selector": "main article",
    }
    saved = profiles.save_profile(spec)
    assert saved["frontier"] == "auto" and not saved["builtin"]
    assert profiles.get_profile("my-blog")["content_selector"] == "main article"
    with pytest.raises(ValueError):
        profiles.save_profile({**spec, "id": "aman-primers"})
    with pytest.raises(ValueError):
        profiles.save_profile({**spec, "content_selector": "div > p"})
    assert profiles.delete_profile("my-blog")


# --- HTTP ----------------------------------------------------------------------


def test_http_item_lifecycle_keeps_every_field(client):
    made = client.post(
        "/api/kb/items",
        json={
            "url": "https://h.test/post",
            "kind": "article",
            "tags": ["ml"],
            "collections": ["reading"],
            "notes": "n",
            "starred": True,
        },
    )
    assert made.status_code == 200, made.text
    body = made.json()
    for key in ("index_status", "collections", "starred", "progress", "chunk_count"):
        assert key in body
    assert body["collections"] == ["reading"] and body["starred"] is True
    assert body["index_status"] == "none"

    patched = client.patch(
        f"/api/kb/items/{body['id']}", json={"status": "done", "collections": []}
    ).json()
    assert patched["status"] == "done" and patched["completed_at"]
    assert patched["collections"] == []

    look = client.get("/api/kb/lookup", params={"url": "http://h.test/post/"}).json()
    assert look["item"]["id"] == body["id"]
    assert client.get("/api/kb/summary").json()["items"] == 1
    assert client.delete(f"/api/kb/items/{body['id']}").json()["deleted"] == 1
    assert client.get(
        "/api/kb/lookup", params={"url": "https://h.test/post"}
    ).json() == {"item": None}


def test_http_rejects_non_http_urls(client):
    assert (
        client.post("/api/kb/items", json={"url": "file:///etc/passwd"}).status_code
        == 400
    )


def test_http_search_503_when_embedder_down(client, monkeypatch):
    _indexed("https://e.test/a", "kb-pages", [("A", "x")])

    async def _fallback(text: str, **_kw):
        return _vec(text), "local-fallback"

    monkeypatch.setattr("backend.modules.database.embeddings.get_embedding", _fallback)
    assert client.post("/api/kb/search", json={"query": "x"}).status_code == 503
