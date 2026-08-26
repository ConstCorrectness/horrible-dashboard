"""Doc sets: the scripted-archive CSP, link rewriting, the routes, and crawl bounds.

The CSP assertions are made against the **HTTP response headers**, not against
`page_csp()`'s return value. The helper being right is not the claim that matters —
the claim is that a browser receives `allow-scripts` for a scripted archive and does
not for an inert one, and only a response can prove that.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.artifacts import store as artifact_store
from backend.modules.docviewer import crawl, store


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---- the security decision -------------------------------------------------


def test_scripted_page_is_served_allow_scripts(client: TestClient) -> None:
    scripted = artifact_store.store_bytes(
        b"<html><body><script>window.ran=1</script></body></html>",
        kind="page",
        mime="text/html",
        filename="scripted.html",
        meta={"title": "Scripted", "scripts": True},
    )
    res = client.get(f"/api/artifacts/{scripted['id']}")
    assert res.status_code == 200
    csp = res.headers["content-security-policy"]
    assert "sandbox allow-scripts" in csp
    # The containment: an opaque origin. `allow-same-origin` alongside `allow-scripts`
    # would void the sandbox and hand the page our origin.
    assert "allow-same-origin" not in csp
    # And no network of its own — everything it may render was inlined at capture.
    assert "default-src 'none'" in csp


def test_unmarked_page_stays_inert(client: TestClient) -> None:
    """A `research` capture is sanitized, so it must keep the no-script treatment.

    The flag is opt-in precisely so that enabling scripts for doc sets cannot
    retroactively enable them for every page ever saved.
    """
    inert = artifact_store.store_bytes(
        b"<html><body>saved</body></html>",
        kind="page",
        mime="text/html",
        filename="inert.html",
        meta={"title": "Inert"},
    )
    res = client.get(f"/api/artifacts/{inert['id']}")
    csp = res.headers["content-security-policy"]
    assert csp.startswith("sandbox;")
    assert "allow-scripts" not in csp


# ---- link rewriting --------------------------------------------------------


def _make_set(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "title": "Example docs",
        "seed_url": "https://docs.example.dev/guide/intro",
        "prefix": "https://docs.example.dev/guide/",
        "library": "docs-example",
        "max_pages": 50,
    }
    kwargs.update(overrides)
    return store.create_set(**kwargs)


def test_default_prefix_is_the_seeds_directory() -> None:
    assert (
        crawl.default_prefix("https://docs.example.dev/guide/intro")
        == "https://docs.example.dev/guide/"
    )
    # A seed that already names a directory keeps its whole path.
    assert (
        crawl.default_prefix("https://docs.example.dev/guide/")
        == "https://docs.example.dev/guide/"
    )


def test_intra_set_links_point_at_sibling_archives() -> None:
    doc = _make_set()
    html = (
        "<html><body>"
        '<a href="/guide/install">install</a>'
        '<a href="/guide/api#methods">api</a>'
        '<a href="https://example.com/pricing">pricing</a>'
        '<a href="#top">top</a>'
        "</body></html>"
    )
    out = crawl.rewrite_links(
        html, "https://docs.example.dev/guide/intro", doc["id"], doc["prefix"]
    )

    install_id = store.page_id(doc["id"], "https://docs.example.dev/guide/install")
    api_id = store.page_id(doc["id"], "https://docs.example.dev/guide/api")
    assert f'href="/api/docviewer/pages/{install_id}/content"' in out
    # The fragment survives: it addresses a section of the sibling page, and dropping
    # it would land every deep link at the top of the document.
    assert f'href="/api/docviewer/pages/{api_id}/content#methods"' in out
    # Out of scope: left absolute, and inert under the archive's CSP.
    assert 'href="https://example.com/pricing"' in out
    # A pure fragment is a link within this page and must not be rewritten.
    assert 'href="#top"' in out


def test_rewriting_is_stable_before_the_target_exists() -> None:
    """The id is derived from the URL, so two pages can link to each other.

    This is the whole reason archives address siblings by page id rather than by
    artifact id: an artifact id is a hash of the bytes, and mutual links would need
    each page's bytes to compute the other's.
    """
    doc = _make_set()
    a = crawl.rewrite_links(
        '<a href="/guide/b">b</a>',
        "https://docs.example.dev/guide/a",
        doc["id"],
        doc["prefix"],
    )
    b = crawl.rewrite_links(
        '<a href="/guide/a">a</a>',
        "https://docs.example.dev/guide/b",
        doc["id"],
        doc["prefix"],
    )
    assert store.page_id(doc["id"], "https://docs.example.dev/guide/b") in a
    assert store.page_id(doc["id"], "https://docs.example.dev/guide/a") in b


def test_scope_ignores_www_and_scheme() -> None:
    doc = _make_set()
    out = crawl.rewrite_links(
        '<a href="http://www.docs.example.dev/guide/install">install</a>',
        "https://docs.example.dev/guide/intro",
        doc["id"],
        doc["prefix"],
    )
    assert "/api/docviewer/pages/" in out


# ---- routes ----------------------------------------------------------------


def test_create_set_rejects_a_private_host(client: TestClient) -> None:
    """A doc-set crawl drives a real browser at a user-supplied URL, so it takes the
    same egress check navigation does rather than a laxer one of its own."""
    res = client.post(
        "/api/docviewer/sets", json={"seed_url": "http://127.0.0.1:8000/"}
    )
    assert res.status_code == 400


def test_create_set_starts_a_crawl(client: TestClient, monkeypatch) -> None:
    # `_check_host_public` resolves the host for real, and `example.dev` does not
    # exist. Its own behaviour is covered by `test_create_set_rejects_a_private_host`,
    # which needs no DNS.
    import backend.modules.docviewer.routes as docviewer_routes

    monkeypatch.setattr(docviewer_routes, "_check_host_public", lambda _host: None)
    started: list[str] = []
    monkeypatch.setattr(
        crawl, "start_crawl", lambda set_id, *a, **k: started.append(set_id) or True
    )
    res = client.post(
        "/api/docviewer/sets",
        json={"seed_url": "https://docs.example.dev/guide/intro", "max_pages": 5},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["prefix"] == "https://docs.example.dev/guide/"
    assert body["title"] == "docs.example.dev"
    assert body["max_pages"] == 5
    assert started == [body["id"]]

    listed = client.get("/api/docviewer/sets").json()["sets"]
    assert [s["id"] for s in listed] == [body["id"]]


def test_create_set_really_schedules_the_crawl(client: TestClient, monkeypatch) -> None:
    """The route must reach `asyncio.create_task` for real.

    Stubbing `start_crawl` (as the test above does, to check what it is called with)
    hides the thing that actually broke: a plain `def` endpoint runs in FastAPI's
    threadpool, where there is no running loop, and `create_task` raises with
    `RuntimeError: no running event loop`. Keeping the real `start_crawl` and stubbing
    one level deeper is what makes that a failure rather than a 500 in production.
    """
    import backend.modules.docviewer.routes as docviewer_routes

    monkeypatch.setattr(docviewer_routes, "_check_host_public", lambda _host: None)
    ran: list[str] = []

    async def _fake_run(set_id: str, max_depth: int) -> None:
        ran.append(set_id)

    monkeypatch.setattr(crawl, "_run_crawl", _fake_run)

    res = client.post(
        "/api/docviewer/sets",
        json={"seed_url": "https://docs.example.dev/guide/intro"},
    )
    assert res.status_code == 200
    set_id = res.json()["id"]
    # The claim is that `create_task` was reached and the task actually ran. It may
    # already have finished (and popped itself out of `_running`) by the time the
    # response came back, so wait on the effect rather than on the bookkeeping —
    # each request hands the app's loop another turn.
    for _ in range(50):
        if ran:
            break
        client.get("/api/docviewer/sets")
    assert ran == [set_id]


def test_page_content_serves_the_archive_with_scripts(client: TestClient) -> None:
    doc = _make_set()
    page = store.upsert_page(
        set_id=doc["id"],
        url="https://docs.example.dev/guide/intro",
        title="Intro",
        status="pending",
    )
    artifact = artifact_store.store_bytes(
        b"<html><body><script>1</script>intro</body></html>",
        kind="page",
        mime="text/html",
        filename="intro.html",
        meta={"title": "Intro", "scripts": True},
    )
    store.mark_captured(
        page["id"], title="Intro", artifact_id=artifact["id"], source_id=None, size=42
    )

    res = client.get(f"/api/docviewer/pages/{page['id']}/content")
    assert res.status_code == 200
    assert b"intro" in res.content
    csp = res.headers["content-security-policy"]
    assert "sandbox allow-scripts" in csp
    assert "allow-same-origin" not in csp


def test_page_content_renders_html_when_not_captured(client: TestClient) -> None:
    """A link can point at a page the crawl never reached. It resolves inside the
    archive frame, so the answer has to be renderable HTML — but still a 404."""
    doc = _make_set()
    page = store.upsert_page(
        set_id=doc["id"],
        url="https://docs.example.dev/guide/missing",
        title="Missing",
        status="pending",
    )
    res = client.get(f"/api/docviewer/pages/{page['id']}/content")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("text/html")
    assert b"Not captured" in res.content


def test_delete_set_removes_its_artifacts(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(crawl, "is_running", lambda _set_id: False)
    doc = _make_set()
    page = store.upsert_page(
        set_id=doc["id"],
        url="https://docs.example.dev/guide/intro",
        title="Intro",
        status="pending",
    )
    artifact = artifact_store.store_bytes(
        b"<html>intro</html>",
        kind="page",
        mime="text/html",
        filename="intro.html",
        meta={"scripts": True},
    )
    store.mark_captured(
        page["id"], title="Intro", artifact_id=artifact["id"], source_id=None, size=18
    )

    assert client.delete(f"/api/docviewer/sets/{doc['id']}").status_code == 200
    assert store.get_set(doc["id"]) is None
    assert store.list_pages(doc["id"]) == []
    assert artifact_store.get_artifact(artifact["id"]) is None


def test_delete_refuses_while_a_crawl_runs(client: TestClient, monkeypatch) -> None:
    doc = _make_set()
    monkeypatch.setattr(crawl, "is_running", lambda set_id: set_id == doc["id"])
    assert client.delete(f"/api/docviewer/sets/{doc['id']}").status_code == 409


# ---- crawl bounds ----------------------------------------------------------


class _FakeSession:
    """Stands in for a headless `BrowserSession`, one page deep in links.

    Every page links to the next and to an off-site URL, so a crawl that respected
    neither the cap nor the prefix would run forever.
    """

    def __init__(self) -> None:
        self.visited: list[str] = []
        self.url = ""

    async def submit(self, op: str, args: dict[str, Any]) -> Any:
        if op == "navigate":
            self.url = str(args["url"])
            self.visited.append(self.url)
            return None
        if op == "wait":
            return None
        if op == "capture":
            assert args["keep_scripts"] is True
            assert args["store"] is False
            n = len(self.visited)
            return {
                "url": self.url,
                "title": f"page {n}",
                "text": f"body {n}",
                "html": (
                    "<html><body>"
                    f'<a href="https://docs.example.dev/guide/p{n}">next</a>'
                    '<a href="https://elsewhere.example/off">off</a>'
                    "</body></html>"
                ),
            }
        raise AssertionError(f"unexpected op {op}")


@pytest.fixture
def fake_crawl(monkeypatch) -> _FakeSession:
    session = _FakeSession()

    class _Manager:
        async def open_headless(self, _key: str, profile: str = "default") -> Any:
            return session

        def close_headless(self, _key: str) -> None:
            return None

    import backend.modules.browser.session as browser_session

    monkeypatch.setattr(browser_session, "browser_manager", _Manager())

    class _AllowAll:
        async def allowed(self, _url: str) -> bool:
            return True

        async def crawl_delay(self, _url: str) -> float | None:
            return None

    monkeypatch.setattr(crawl, "RobotsCache", lambda: _AllowAll())

    # Ingest is exercised in the library's own suite; here it would embed text.
    async def _no_ingest(**_kwargs: Any) -> str | None:
        return "source-id"

    monkeypatch.setattr(crawl, "_ingest_page", _no_ingest)
    return session


def test_crawl_stops_at_max_pages(fake_crawl: _FakeSession) -> None:
    doc = _make_set(max_pages=3)
    asyncio.run(crawl._run_crawl(doc["id"], max_depth=10))

    captured = [p for p in store.list_pages(doc["id"]) if p["status"] == "captured"]
    assert len(captured) == 3
    assert store.get_set(doc["id"])["page_count"] == 3
    # The off-site link was offered on every page and never followed.
    assert all("elsewhere.example" not in url for url in fake_crawl.visited)


def test_crawl_respects_robots_disallow(fake_crawl: _FakeSession, monkeypatch) -> None:
    class _DenyAll:
        async def allowed(self, _url: str) -> bool:
            return False

        async def crawl_delay(self, _url: str) -> float | None:
            return None

    monkeypatch.setattr(crawl, "RobotsCache", lambda: _DenyAll())
    doc = _make_set(max_pages=5)
    asyncio.run(crawl._run_crawl(doc["id"], max_depth=3))

    assert fake_crawl.visited == []
    assert store.list_pages(doc["id"]) == []


def test_crawl_stops_at_max_depth(fake_crawl: _FakeSession) -> None:
    doc = _make_set(max_pages=50)
    asyncio.run(crawl._run_crawl(doc["id"], max_depth=1))

    captured = [p for p in store.list_pages(doc["id"]) if p["status"] == "captured"]
    # The seed (depth 0) plus the one page it links to (depth 1).
    assert len(captured) == 2
    assert max(p["depth"] for p in captured) == 1
