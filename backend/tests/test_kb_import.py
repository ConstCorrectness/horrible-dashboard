"""Site imports: the recipe crawl end to end against a fake headless browser.

Two fixtures model the two real shapes. `aman` is a flat primer site whose index is
its home page, outside the scope it leads to, and which serves one primer at two
paths with a `rel=canonical` between them. `apxml` is a course whose chapters and
lessons nest by URL path and are listed in a sitemap.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.database.embeddings import get_local_fallback_embedding
from backend.modules.docviewer import crawl, site_crawl
from backend.modules.docviewer import store as docstore
from backend.modules.docviewer.recipe import Recipe
from backend.modules.docviewer.sitemap import parse_sitemap
from backend.modules.kb import imports, profiles, service, store
from backend.modules.kb.models import SearchRequest
from backend.modules.library import store as library_store


def _primer(title: str, body: str, canonical: str) -> str:
    return (
        f'<html><head><title>{title}</title><link rel="canonical" href="{canonical}">'
        f'</head><body><article class="post-content"><h1>{title}</h1>'
        f'<h2 id="overview">Overview</h2><p>{body}</p>'
        '<script type="math/tex">x^2</script></article></body></html>'
    )


AMAN = {
    "https://aman.ai/": (
        "<html><body>"
        '<a href="primers/transformers">T</a><a href="primers/RAG">R</a>'
        '<a href="primers/rag-again">R2</a><a href="https://twitter.com/x">tw</a>'
        "</body></html>"
    ),
    "https://aman.ai/primers/transformers": _primer(
        "Transformers",
        "Self attention mixes tokens.",
        "https://aman.ai/primers/ai/transformers/",
    ),
    "https://aman.ai/primers/RAG": _primer(
        "RAG", "Retrieval grounds generation.", "https://aman.ai/primers/ai/RAG/"
    ),
    # Same canonical as RAG: a second path to the same primer.
    "https://aman.ai/primers/rag-again": _primer(
        "RAG", "Retrieval grounds generation.", "https://aman.ai/primers/ai/RAG/"
    ),
}

APX_ROOT = "https://apxml.com/courses/vdb"
APX = {
    APX_ROOT: "<html><head><title>Vector DBs</title></head><body>"
    '<div class="course-content"><h1>Vector DBs</h1><p>Course intro.</p></div></body></html>',
    f"{APX_ROOT}/ch1": "<html><head><title>Chapter 1</title></head><body>"
    '<div class="course-content"><p>Chapter overview.</p></div></body></html>',
    f"{APX_ROOT}/ch1/cosine": "<html><head><title>Cosine</title></head><body>"
    '<div class="course-content"><h3>Cosine Similarity</h3><p>Angle based.</p></div>'
    "</body></html>",
    f"{APX_ROOT}/ch1/dot": "<html><head><title>Dot</title></head><body>"
    '<div class="course-content"><h3>Dot Product</h3><p>Magnitude matters.</p></div>'
    "</body></html>",
}


class _FakeSession:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.url = ""
        self.visited: list[str] = []
        self.captures: list[str] = []

    async def submit(self, op: str, args: dict[str, Any]) -> Any:
        if op == "navigate":
            self.url = str(args["url"]).rstrip("/") or str(args["url"])
            if str(args["url"]).endswith("aman.ai/"):
                self.url = "https://aman.ai/"
            self.visited.append(self.url)
            return None
        if op == "wait":
            return None
        if op == "eval":
            if "outerHTML" in args["js"]:
                return {"result": self.pages.get(self.url, "")}
            return {"result": 0}
        if op == "capture":
            assert args["store"] is False
            self.captures.append(self.url)
            if self.url not in self.pages:
                raise RuntimeError(f"404 {self.url}")
            html = self.pages[self.url]
            title = (
                html.split("<title>")[1].split("</title>")[0]
                if "<title>" in html
                else self.url
            )
            return {"url": self.url, "title": title, "html": html}
        raise AssertionError(f"unexpected op {op}")


class _Robots:
    signal: str | None = None

    async def allowed(self, _url: str) -> bool:
        return True

    async def crawl_delay(self, _url: str) -> float | None:
        return None

    async def content_signal(self, _url: str) -> str | None:
        return self.signal

    async def sitemaps(self, _url: str) -> list[str]:
        return []


@pytest.fixture
def harness(monkeypatch):
    state: dict[str, Any] = {"pages": {}, "session": None, "robots": _Robots()}

    class _Manager:
        async def open_headless(
            self, _key: str, profile: str = "default", *, user_agent: str | None = None
        ) -> Any:
            state["user_agent"] = user_agent
            state["session"] = _FakeSession(state["pages"])
            return state["session"]

        def close_headless(self, _key: str) -> None:
            return None

    import backend.modules.browser.session as browser_session

    monkeypatch.setattr(browser_session, "browser_manager", _Manager())
    monkeypatch.setattr(site_crawl, "RobotsCache", lambda: state["robots"])
    # No politeness delay against a fake browser.
    monkeypatch.setattr(site_crawl, "get_value", lambda key, default=None: 0.0)
    monkeypatch.setattr(
        "backend.modules.docviewer.routes._check_host_public", lambda _h: None
    )

    async def _embed_many(texts: list[str]):
        return [get_local_fallback_embedding(t) for t in texts], "test/embed"

    async def _embed_one(text: str):
        return get_local_fallback_embedding(text), "test/embed"

    monkeypatch.setattr("backend.modules.library.ingest.get_embeddings", _embed_many)
    monkeypatch.setattr("backend.modules.database.embeddings.get_embedding", _embed_one)

    # Run the crawl inline instead of as a detached task.
    def _start(set_id: str, max_depth: int = 6, *, force: bool = False) -> bool:
        state.setdefault("runs", []).append((set_id, max_depth, force))
        return True

    monkeypatch.setattr(crawl, "start_crawl", _start)
    return state


def _run(state: dict[str, Any]) -> None:
    set_id, depth, force = state["runs"].pop()
    asyncio.run(crawl._run_crawl(set_id, depth, force))


def _import(state, **kwargs) -> dict[str, Any]:
    started = asyncio.run(imports.start_import(**kwargs))
    _run(state)
    return started


# --- pure pieces ---------------------------------------------------------------


def test_parse_sitemap_urlset_and_index():
    urls, children = parse_sitemap(
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://a.test/x</loc></url><url><loc> https://a.test/y </loc></url>"
        "</urlset>"
    )
    assert urls == ["https://a.test/x", "https://a.test/y"] and children == []
    urls, children = parse_sitemap(
        "<sitemapindex><sitemap><loc>https://a.test/s1.xml</loc></sitemap></sitemapindex>"
    )
    assert urls == [] and children == ["https://a.test/s1.xml"]
    assert parse_sitemap("not xml at all <<<") == ([], [])


def test_recipe_url_path_tree_and_kinds():
    r = Recipe(
        start_urls=[APX_ROOT],
        hierarchy="url_path",
        path_root="/courses/vdb",
        path_kinds=["course", "chapter", "lesson"],
    )
    assert r.is_root(APX_ROOT)
    assert r.path_parent(f"{APX_ROOT}/ch1/cosine") == f"{APX_ROOT}/ch1"
    assert r.path_parent(f"{APX_ROOT}/ch1") == APX_ROOT
    assert r.path_parent(APX_ROOT) is None
    assert r.kind_for(f"{APX_ROOT}/ch1/cosine") == "lesson"
    assert r.kind_for(f"{APX_ROOT}/ch1/cosine/deeper") == "lesson"  # clamps


# --- aman-shaped import --------------------------------------------------------


def test_flat_import_keeps_primers_drops_index_and_duplicate(harness):
    harness["pages"] = AMAN
    started = _import(harness, url="https://aman.ai/primers/ai/RAG/")
    assert started["plan"]["id"] == "aman-primers"

    pages = docstore.list_pages(started["set_id"])
    captured = {p["url"] for p in pages if p["status"] == "captured"}
    assert captured == {
        "https://aman.ai/primers/transformers",
        "https://aman.ai/primers/RAG",
    }
    dup = [p for p in pages if p["status"] == "failed"]
    assert len(dup) == 1 and "duplicate" in dup[0]["error"]
    # The index page was navigated, not kept; the off-site link never visited.
    assert "https://aman.ai/" not in captured
    assert not any("twitter" in u for u in harness["session"].visited)

    root = service.item_model(started["root_item_id"])
    assert root.kind == "site" and root.collections == ["ml"]
    assert root.progress.total == 2 and root.child_count == 2
    children, _ = store.list_items(parent_id=root.id)
    assert {c["kind"] for c in children} == {"primer"}
    rag = store.get_by_url("https://aman.ai/primers/ai/RAG/")  # by declared canonical
    assert rag is not None and rag["parent_id"] == root.id
    # Text landed, structured: section + TeX survive.
    source = library_store.get_source(rag["source_id"])
    assert source["status"] == "ready" and source["chunk_count"] >= 1
    chunks = library_store.chunk_docs_for(source)
    assert "$x^2$" in chunks[0]["text"]
    assert service.item_model(rag["id"]).index_status == "ready"


def test_import_is_searchable_with_citations(harness):
    harness["pages"] = AMAN
    _import(harness, profile_id="aman-primers")
    res = asyncio.run(
        service.search(SearchRequest(query="Retrieval grounds generation.", limit=3))
    )
    assert res.passages, res
    top = res.passages[0]
    assert top.url == "https://aman.ai/primers/ai/RAG/"
    assert top.section == "Overview"
    assert top.link == "https://aman.ai/primers/ai/RAG/#overview"
    assert top.docpage_id


def test_reimport_resumes_without_recapturing(harness):
    harness["pages"] = AMAN
    first = _import(harness, profile_id="aman-primers")
    first_captures = list(harness["session"].captures)
    second = _import(harness, profile_id="aman-primers")
    assert second["resumed"] and second["set_id"] == first["set_id"]
    # Only the duplicate path is re-tried; captured primers are skipped.
    assert set(harness["session"].captures) <= {"https://aman.ai/primers/rag-again"}
    assert len(first_captures) == 3
    forced = asyncio.run(imports.start_import(profile_id="aman-primers", force=True))
    _run(harness)
    assert forced["resumed"]
    assert len(set(harness["session"].captures)) == 3
    # A forced recapture replaced, rather than duplicated, the page text.
    rows, _ = store.list_items(docset_id=first["set_id"])
    sources = [r["source_id"] for r in rows if r["source_id"]]
    assert len(sources) == len(set(sources)) == 2
    assert all(library_store.get_source(s) for s in sources)


def test_ai_input_no_archives_but_does_not_embed(harness):
    harness["pages"] = AMAN
    harness["robots"].signal = "ai-train=no, search=yes, ai-input=no"
    started = _import(harness, profile_id="aman-primers")
    rows, _ = store.list_items(docset_id=started["set_id"])
    pages = [r for r in rows if r["id"] != started["root_item_id"]]
    assert pages and all(r["source_id"] is None for r in pages)
    assert all(r["artifact_id"] for r in pages)
    assert {service.item_model(r["id"]).index_status for r in pages} == {"skipped"}
    assert "ai-input=no" in (docstore.get_set(started["set_id"])["error"] or "")


def test_adopts_an_existing_bookmark_keeping_user_state(harness):
    harness["pages"] = AMAN
    mine = store.create_item(
        url="https://aman.ai/primers/transformers",
        kind="bookmark",
        notes="read twice",
        status="done",
        starred=True,
    )
    _import(harness, profile_id="aman-primers")
    adopted = store.get_item(mine["id"])
    assert adopted["kind"] == "primer"  # a bookmark takes the import's kind
    assert adopted["notes"] == "read twice" and adopted["status"] == "done"
    assert adopted["starred"] and adopted["docset_id"]


def test_deleting_the_root_removes_the_site(harness, monkeypatch):
    harness["pages"] = AMAN
    started = _import(harness, profile_id="aman-primers")
    client = TestClient(app)
    res = client.delete(f"/api/kb/items/{started['root_item_id']}")
    assert res.status_code == 200 and res.json()["deleted"] == 3
    assert docstore.get_set(started["set_id"]) is None
    assert service.kb_libraries() == []
    assert library_store.list_sources(library="kb-aman.ai") == []


def test_deleting_the_doc_set_detaches_items(harness):
    harness["pages"] = AMAN
    started = _import(harness, profile_id="aman-primers")
    client = TestClient(app)
    assert client.delete(f"/api/docviewer/sets/{started['set_id']}").status_code == 200
    rows, _ = store.list_items(parent_id=started["root_item_id"])
    assert rows and all(
        r["docset_id"] is None and r["index_mode"] == "link" for r in rows
    )


# --- apxml-shaped import -------------------------------------------------------


def test_course_tree_from_sitemap_and_url_path(harness, monkeypatch):
    harness["pages"] = APX

    async def _sitemap(_url: str, in_scope, cap: int = 0):
        listed = [
            f"{APX_ROOT}/ch1/cosine",  # a lesson listed before its chapter
            f"{APX_ROOT}/ch1",
            f"{APX_ROOT}/ch1/dot",
            f"{APX_ROOT}/ch1/quiz",
            "https://apxml.com/courses/other-course/ch1",
        ]
        return [u for u in listed if in_scope(u)]

    monkeypatch.setattr(site_crawl, "sitemap_urls", _sitemap)
    started = _import(harness, url=f"{APX_ROOT}/ch1/cosine")
    assert started["plan"]["id"] == "apxml-course"

    tree = service.tree(started["root_item_id"])
    assert tree.item.kind == "course"
    assert tree.item.title == "Vector DBs"  # the slug title was replaced
    assert [c.item.kind for c in tree.children] == ["chapter"]
    lessons = tree.children[0].children
    assert {lesson.item.title for lesson in lessons} == {"Cosine", "Dot"}
    assert all(lesson.item.kind == "lesson" for lesson in lessons)
    # The quiz was denied and the other course was out of scope.
    captured = {p["url"] for p in docstore.list_pages(started["set_id"])}
    assert not any(u.endswith("/quiz") or "other-course" in u for u in captured)

    store.update_item(lessons[0].item.id, status="done")
    course = service.item_model(started["root_item_id"])
    assert course.progress.done == 1 and course.progress.total == 2


def test_http_import_and_profile_match(harness):
    harness["pages"] = AMAN
    client = TestClient(app)
    match = client.get(
        "/api/kb/profiles/match", params={"url": "https://aman.ai/primers/ai/RAG/"}
    ).json()
    assert match["profile"]["id"] == "aman-primers"
    assert client.get(
        "/api/kb/profiles/match", params={"url": "https://x.test/"}
    ).json() == {
        "profile": None,
        "params": {},
    }
    res = client.post(
        "/api/kb/imports", json={"profile_id": "aman-primers", "max_pages": 5}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"root_item_id", "set_id", "resumed", "plan"}
    assert docstore.get_set(body["set_id"])["max_pages"] == 5
    bad = client.post("/api/kb/imports", json={"profile_id": "apxml-course"})
    assert bad.status_code == 400 and "needs course" in bad.json()["detail"]


# --- capture: images + inline-style fonts --------------------------------------


def test_shrink_image_downsizes_big_rasters_and_keeps_small_ones():
    import io

    from PIL import Image

    from backend.modules.research.capture import SHRINK_MAX_EDGE, shrink_image

    noisy = Image.effect_noise((3000, 2000), 64).convert("RGB")
    buf = io.BytesIO()
    noisy.save(buf, format="PNG")
    data = buf.getvalue()
    out, mime = shrink_image(data, "image/png")
    assert mime == "image/webp" and len(out) < len(data)
    with Image.open(io.BytesIO(out)) as img:
        assert max(img.size) == SHRINK_MAX_EDGE

    tiny = io.BytesIO()
    Image.new("RGB", (10, 10)).save(tiny, format="PNG")
    assert shrink_image(tiny.getvalue(), "image/png") == (tiny.getvalue(), "image/png")
    assert shrink_image(b"<svg/>" * 50_000, "image/svg+xml")[1] == "image/svg+xml"
    assert shrink_image(b"not an image" * 20_000, "image/jpeg")[1] == "image/jpeg"


def test_inline_style_fonts_are_listed_and_inlined():
    from backend.modules.research.capture import (
        INLINE_CSS_ASSET_KIND,
        build_page,
        list_resources,
    )

    html = (
        "<html><head><style>@font-face{font-family:M;"
        'src:url("https://cdn.test/f.woff2") format("woff2")}</style></head>'
        "<body><mjx-container>x</mjx-container></body></html>"
    )
    plan = list_resources(html, "https://site.test/p")
    assert plan == {"https://cdn.test/f.woff2": INLINE_CSS_ASSET_KIND}
    built = build_page(
        html,
        "https://site.test/p",
        {"https://cdn.test/f.woff2": (b"FONT", "font/woff2")},
    )
    assert "data:font/woff2;base64," in built
    assert "cdn.test/f.woff2" not in built


def test_titles_are_stripped_by_the_profile():
    r = Recipe(start_urls=["https://aman.ai/"], title_strip=[r"^Primers\s*•\s*"])
    assert r.clean_title("Primers • Retrieval Augmented Generation") == (
        "Retrieval Augmented Generation"
    )
    assert r.clean_title("Primers • ") == "Primers • "  # never strips to nothing


def test_mathjax_speech_attributes_are_stripped_but_tex_stamp_kept():
    from backend.modules.research.capture import build_page

    html = (
        '<html><body><mjx-container class="MathJax" data-tex="x^2" '
        'data-semantic-speech="x squared" ctxtmenu_counter="0">'
        '<mjx-math data-semantic-type="superscript"><mjx-c class="mjx-c78"></mjx-c>'
        "</mjx-math></mjx-container><p data-semantic-keep='yes'>t</p></body></html>"
    )
    built = build_page(html, "https://s.test/", {})
    assert "data-semantic-speech" not in built and "data-semantic-type" not in built
    assert "ctxtmenu_counter" not in built
    assert 'data-tex="x^2"' in built and "mjx-c78" in built
    assert "data-semantic-keep" in built  # only MathJax's own elements are slimmed


# --- batching, re-index, MathJax 3 ---------------------------------------------


def _many_primers(n: int) -> dict[str, str]:
    links = "".join(f'<a href="primers/p{i}">P{i}</a>' for i in range(n))
    pages = {"https://aman.ai/": f"<html><body>{links}</body></html>"}
    for i in range(n):
        pages[f"https://aman.ai/primers/p{i}"] = (
            f"<html><head><title>Primer {i}</title></head><body>"
            f'<article class="post-content"><h2 id="s">Section</h2>'
            # MathJax 3, exactly as captured from aman.ai: the container carries
            # class="MathJax", and its TeX survives only as the stamped data-tex.
            f'<p>Topic {i} scales as <mjx-container class="MathJax CtxtMenu_Attached_0" '
            f'jax="CHTML" data-tex="\\sqrt{{d_{i}}}"><mjx-math>d</mjx-math>'
            "</mjx-container> here.</p></article></body></html>"
        )
    return pages


def test_a_long_crawl_commits_in_batches_not_once_at_the_end(harness, monkeypatch):
    from backend.modules.library import ingest

    commits: list[int] = []
    real = ingest.commit_prepared

    def counting(prepared):
        commits.append(len(prepared))
        return real(prepared)

    monkeypatch.setattr(ingest, "commit_prepared", counting)
    harness["pages"] = _many_primers(11)
    _import(harness, profile_id="aman-primers")
    # 11 pages = one full batch of 8 mid-crawl, then the remaining 3 at the end. A
    # single commit of 11 was the bug: nothing searchable until the crawl ended.
    assert commits == [site_crawl.INGEST_BATCH_PAGES, 3]


def test_mathjax3_tex_survives_a_real_import(harness):
    harness["pages"] = _many_primers(1)
    _import(harness, profile_id="aman-primers")
    item = store.get_by_url("https://aman.ai/primers/p0")
    chunks = library_store.chunk_docs_for(library_store.get_source(item["source_id"]))
    assert "$\\sqrt{d_0}$" in chunks[0]["text"]


def test_reindex_rebuilds_text_from_the_archives_without_crawling(harness, monkeypatch):
    harness["pages"] = _many_primers(3)

    # The embedder is down during the import: pages archived, nothing indexed.
    async def _down(texts):
        return [get_local_fallback_embedding(t) for t in texts], "local-fallback"

    monkeypatch.setattr("backend.modules.library.ingest.get_embeddings", _down)
    started = _import(harness, profile_id="aman-primers")
    set_id = started["set_id"]
    rows, _ = store.list_items(docset_id=set_id)
    pages = [r for r in rows if r["id"] != started["root_item_id"]]
    assert len(pages) == 3 and all(r["artifact_id"] for r in pages)
    assert not any(
        (library_store.get_source(r["source_id"]) or {}).get("status") == "ready"
        for r in pages
        if r["source_id"]
    )

    # Back up; re-index. No browser may be opened for it.
    async def _up(texts):
        return [get_local_fallback_embedding(t) for t in texts], "test/embed"

    monkeypatch.setattr("backend.modules.library.ingest.get_embeddings", _up)
    harness["session"] = None
    asyncio.run(site_crawl.run_reindex(set_id))
    assert harness["session"] is None

    for row in store.list_items(docset_id=set_id)[0]:
        if row["id"] == started["root_item_id"]:
            continue
        source = library_store.get_source(row["source_id"])
        assert source["status"] == "ready" and source["chunk_count"] >= 1
        assert service.item_model(row["id"]).index_status == "ready"
    res = asyncio.run(service.search(SearchRequest(query="Topic 1 scales", limit=3)))
    assert res.passages and res.passages[0].url == "https://aman.ai/primers/p1"


def test_reindex_replaces_chunks_rather_than_duplicating(harness):
    harness["pages"] = _many_primers(2)
    started = _import(harness, profile_id="aman-primers")
    item = store.get_by_url("https://aman.ai/primers/p0")
    before = library_store.get_source(item["source_id"])["chunk_count"]
    asyncio.run(site_crawl.run_reindex(started["set_id"]))
    after = store.get_by_url("https://aman.ai/primers/p0")
    assert after["source_id"] == item["source_id"]  # same source, re-filled
    source = library_store.get_source(after["source_id"])
    assert source["chunk_count"] == before
    assert len(library_store.chunk_docs_for(source)) == before


def test_reindex_route_refuses_a_legacy_set_and_a_running_one(harness, monkeypatch):
    client = TestClient(app)
    legacy = docstore.create_set(
        title="Docs",
        seed_url="https://x.dev/docs/",
        prefix="https://x.dev/docs/",
        library="docs-x.dev",
        max_pages=5,
    )
    r = client.post(f"/api/docviewer/sets/{legacy['id']}/reindex")
    assert r.status_code == 400

    harness["pages"] = _many_primers(1)
    started = _import(harness, profile_id="aman-primers")
    monkeypatch.setattr(crawl, "start_reindex", lambda _set_id: False)
    r = client.post(f"/api/kb/imports/{started['root_item_id']}/reindex")
    assert r.status_code == 409


# --- bot checks ---------------------------------------------------------------

CHALLENGE = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<script>window._cf_chl_opt={cvId: '3'};</script>"
    "<noscript>Enable JavaScript and cookies to continue</noscript></body></html>"
)


def test_a_bot_check_stops_the_import_and_archives_nothing(harness):
    """Found live: apxml.com served Cloudflare's "Just a moment..." page to the
    headless browser, and 31 lessons were archived and indexed as that page."""
    pages = _many_primers(3)
    pages["https://aman.ai/primers/p0"] = CHALLENGE
    pages["https://aman.ai/primers/p1"] = CHALLENGE
    harness["pages"] = pages
    started = _import(harness, profile_id="aman-primers")

    doc_set = docstore.get_set(started["set_id"])
    assert doc_set["status"] == "failed"
    assert "Cloudflare bot check" in doc_set["error"]
    # Stopped at the first challenge rather than asking again for every page.
    assert harness["session"].captures == ["https://aman.ai/primers/p0"]
    page = docstore.list_pages(started["set_id"])[0]
    assert page["status"] == "failed" and "Cloudflare" in page["error"]
    rows, _ = store.list_items(docset_id=started["set_id"])
    assert [r["id"] for r in rows] == [started["root_item_id"]]
    assert library_store.list_sources(library="kb-aman.ai") == []


def test_bot_challenge_recognises_interstitials_but_not_ordinary_pages():
    assert site_crawl.bot_challenge("Just a moment...", CHALLENGE) == "Cloudflare"
    assert (
        site_crawl.bot_challenge("Attention Required! | Cloudflare", "<html></html>")
        == "Cloudflare"
    )
    # Cloudflare injects this script into ordinary pages on sites it fronts; a
    # page carrying it is still the page.
    ordinary = (
        "<html><head><title>Attention Is All You Need</title></head><body>"
        '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
        "<p>Just a moment of attention.</p></body></html>"
    )
    assert site_crawl.bot_challenge("Attention Is All You Need", ordinary) is None


def test_a_stop_mid_embed_leaves_no_orphan_source(monkeypatch):
    """A source created for a page and cancelled before commit is linked to
    nothing, so it used to sit at "embedding" forever."""

    async def cancelled(_source_id, _req):
        raise asyncio.CancelledError

    monkeypatch.setattr("backend.modules.library.ingest.prepare_source", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            site_crawl._prepare(
                library="kb-test",
                url="https://t.test/p",
                title="P",
                artifact_id="a1",
                recipe=Recipe(start_urls=["https://t.test/"]),
            )
        )
    assert library_store.list_sources(library="kb-test") == []


def test_an_import_crawl_says_what_it_is(harness):
    """A site asked to allow the importer needs something to recognise it by: the
    named crawler User-Agent, linking to a repository that exists."""
    from backend.modules.search.crawl.robots import USER_AGENT

    harness["pages"] = _many_primers(1)
    _import(harness, profile_id="aman-primers")
    assert harness["user_agent"] == USER_AGENT
    assert USER_AGENT.startswith("horrible-dashboard-crawler/")
    assert "github.com/ConstCorrectness/horrible-dashboard" in USER_AGENT


def test_reader_view_needs_a_content_selector():
    spec = {
        "id": "reader-site",
        "start_urls": ["https://example.org/"],
        "allow_domains": ["example.org"],
        "reader_view": True,
    }
    with pytest.raises(ValueError, match="content_selector"):
        profiles.validate(spec)
    ok = profiles.validate({**spec, "content_selector": "main.article"})
    assert ok["reader_view"] is True
    assert profiles.get_profile("apxml-course")["reader_view"] is True


def test_resuming_an_import_follows_the_current_profile(harness):
    """An edited profile (here `keep_scripts`, turned off for apxml after 15 MB
    archives) used to reach only new imports: a resume kept the old recipe."""
    harness["pages"] = _many_primers(1)
    first = _import(harness, profile_id="aman-primers")
    stale = dict(docstore.get_set(first["set_id"])["recipe"])
    stale["keep_scripts"] = True
    stale["content_selector"] = "div.stale"
    docstore.update_set(first["set_id"], recipe=stale)

    second = _import(harness, profile_id="aman-primers")
    assert second["resumed"] and second["set_id"] == first["set_id"]
    recipe = docstore.get_set(first["set_id"])["recipe"]
    assert recipe["content_selector"] == "article.post-content"
    assert recipe["keep_scripts"] is False
    assert recipe["kb_root_item_id"] == first["root_item_id"]
