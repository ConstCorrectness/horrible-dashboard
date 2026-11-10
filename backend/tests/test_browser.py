"""Tests for the embedded browser module: the SSRF guard, reader-mode route, and
the history/bookmarks store. No real network — DNS resolution is monkeypatched and
reader-mode fetch is stubbed. The autouse `isolate_data_dir` fixture points the
store at a temp DB per test.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.browser import fetch, store
from backend.modules.browser.fetch import UnsafeUrlError, _validate, safe_fetch_bytes
from backend.modules.library.extract import Article

client = TestClient(app)


# ---- SSRF guard ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "https:///nohost",
    ],
)
def test_ssrf_blocks_internal_and_bad_schemes(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        _validate(url)


def test_ssrf_blocks_dns_name_resolving_to_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A public-looking name that resolves to a private IP must still be rejected.
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))]
    )
    with pytest.raises(UnsafeUrlError):
        _validate("https://sneaky.example.com/")


def test_ssrf_allows_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )
    _validate("https://example.com/")  # no raise


# ---- reader-mode route -----------------------------------------------------


def test_read_returns_extracted_article(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(url: str) -> Article:
        return Article(title="Hello", author="Ada", text="Body text.", url=url)

    monkeypatch.setattr("backend.modules.browser.routes.fetch_readable", fake)
    r = client.get("/api/browser/read", params={"url": "https://example.com/post"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Hello" and body["author"] == "Ada"
    assert body["text"] == "Body text."


def test_read_rejects_unsafe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(url: str) -> Article:
        raise UnsafeUrlError("non-public")

    monkeypatch.setattr("backend.modules.browser.routes.fetch_readable", boom)
    r = client.get("/api/browser/read", params={"url": "http://127.0.0.1/"})
    assert r.status_code == 400


def test_safe_fetch_html_reuses_library_extract() -> None:
    # extract_article is the shared, network-free extractor the fetch composes.
    art = fetch.extract_article("<title>T</title><p>Words here.</p>", "https://x.test/")
    assert art.title == "T"
    assert "Words here" in art.text


# ---- history + bookmarks store (via HTTP) -----------------------------------


def test_history_crud_and_dedup() -> None:
    assert (
        client.post(
            "/api/browser/history", json={"url": "https://a.test", "title": "A"}
        ).status_code
        == 200
    )
    # Same URL again upserts (one row, latest title).
    client.post("/api/browser/history", json={"url": "https://a.test", "title": "A2"})
    entries = client.get("/api/browser/history").json()["entries"]
    assert len(entries) == 1 and entries[0]["title"] == "A2"
    assert client.delete("/api/browser/history").json()["ok"] is True
    assert client.get("/api/browser/history").json()["entries"] == []


def test_bookmarks_crud_and_dedup() -> None:
    made = client.post(
        "/api/browser/bookmarks",
        json={"url": "https://b.test", "title": "B", "tags": ["x"]},
    ).json()
    assert made["tags"] == ["x"]
    # Same URL is idempotent — returns the existing bookmark id.
    dup = client.post(
        "/api/browser/bookmarks", json={"url": "https://b.test", "title": "dup"}
    ).json()
    assert dup["id"] == made["id"]
    assert len(client.get("/api/browser/bookmarks").json()["bookmarks"]) == 1
    assert client.delete(f"/api/browser/bookmarks/{made['id']}").json()["ok"] is True
    assert client.get("/api/browser/bookmarks").json()["bookmarks"] == []
    assert client.delete("/api/browser/bookmarks/nope").status_code == 404


def test_record_visit_requires_url() -> None:
    assert client.post("/api/browser/history", json={"url": "  "}).status_code == 400


def test_store_direct_roundtrip() -> None:
    store.record_visit("https://direct.test", "Direct")
    assert any(e["url"] == "https://direct.test" for e in store.list_history())


# ---- safe_fetch_bytes (CLIP's image sink) ----------------------------------


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x.png",
        "http://localhost:8000/x.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal.png",
        "file:///etc/passwd",
    ],
)
def test_safe_fetch_bytes_enforces_the_same_egress_policy(url):
    """The byte sink is new; the policy behind it must not be."""
    with pytest.raises(UnsafeUrlError):
        asyncio.run(safe_fetch_bytes(url))


@pytest.fixture
def fake_web(monkeypatch: pytest.MonkeyPatch):
    """Serve canned responses to `fetch.py`'s internal client, with a public-IP DNS.

    `_fetch_guarded` builds its own AsyncClient, so the transport is injected by
    swapping the class in the module's namespace.
    """
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )

    def install(content: bytes, content_type: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=content, headers={"content-type": content_type}
            )

        real = httpx.AsyncClient

        class Patched(real):  # type: ignore[misc,valid-type]
            def __init__(self, *a, **kw):
                kw["transport"] = httpx.MockTransport(handler)
                super().__init__(*a, **kw)

        monkeypatch.setattr(fetch.httpx, "AsyncClient", Patched)

    return install


def test_safe_fetch_bytes_returns_actual_bytes(fake_web):
    """Regression: it used to return the httpx.Response itself, so every CLIP embed
    died on "a bytes-like object is required" — while the source still reported
    `ready`, because a CLIP failure is non-fatal by design. Nothing looked broken;
    visual search simply never worked. Stubbing this call in the library tests is
    exactly why they missed it, so assert the real contract here."""
    raw = _png_bytes()
    fake_web(raw, "image/png")

    final_url, data = asyncio.run(safe_fetch_bytes("https://example.com/a.png"))

    assert final_url == "https://example.com/a.png"
    assert isinstance(data, bytes), f"expected bytes, got {type(data).__name__}"
    assert data == raw


def test_safe_fetch_bytes_rejects_a_non_image_content_type(fake_web):
    fake_web(b"<html>nope</html>", "text/html")
    with pytest.raises(UnsafeUrlError, match="content-type"):
        asyncio.run(safe_fetch_bytes("https://example.com/a.png"))


def test_safe_fetch_bytes_caps_an_oversized_body(fake_web):
    """The cap must bite during transfer, not after the body is already in memory."""
    fake_web(b"\x00" * 5000, "image/png")
    with pytest.raises(UnsafeUrlError, match="too large"):
        asyncio.run(safe_fetch_bytes("https://example.com/big.png", max_bytes=1000))


# --- native-engine support: shared page scripts + extract-from-HTML -----------


def test_page_scripts_are_the_files_the_backend_engine_evaluates() -> None:
    from backend.modules.browser import session

    r = client.get("/api/browser/scripts")
    assert r.status_code == 200
    scripts = r.json()["scripts"]
    assert set(scripts) == {"snapshot", "media", "describe"}
    # One source of truth: what the native webview is handed is byte-for-byte what
    # Playwright evaluates, so the two engines number refs identically.
    assert scripts["snapshot"] == session._SNAPSHOT_JS
    assert scripts["media"] == session._MEDIA_JS
    assert scripts["describe"] == session._DESCRIBE_JS
    assert "data-agent-ref" in scripts["snapshot"]


def test_extract_reads_html_without_fetching(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*_a: object, **_k: object) -> None:
        raise AssertionError("extract must not fetch")

    monkeypatch.setattr(fetch, "fetch_readable", no_network)
    body = "<p>" + "Native webviews hand over their live DOM. " * 20 + "</p>"
    html = f"<html><head><title>Live DOM</title></head><body><article>{body}</article></body></html>"
    r = client.post(
        "/api/browser/extract",
        json={"url": "https://example.com/a", "html": html, "title": "Tab title"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["url"] == "https://example.com/a"
    assert "live DOM" in data["text"]
    assert data["title"]


# ---- describe.js: what acting on a ref would do ---------------------------------

_FORMS_PAGE = """<!doctype html><html><body>
<a href="/docs">Docs</a>
<a href="#" onclick="return false">Delete post</a>
<button type="button">Like</button>
<div role="button" tabindex="0" onclick="1"></div>
<form method="get" action="/search"><input name="q"><button>Search</button></form>
<form method="post" action="/login">
  <input name="user" autocomplete="username">
  <input type="password" name="pw" autocomplete="current-password" value="hunter2">
  <button type="submit">Sign in</button>
</form>
<form method="post" action="/pay">
  <input name="cardnumber" autocomplete="cc-number">
  <input type="submit" value="Pay now">
</form>
</body></html>"""


def _chromium_installed() -> bool:
    try:
        from pathlib import Path

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).is_file()
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _chromium_installed(), reason="Chromium is not installed")
def test_describe_reports_what_a_ref_would_do() -> None:
    from playwright.sync_api import sync_playwright

    from backend.modules.browser import session

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(_FORMS_PAGE)
            snap = page.evaluate(session._SNAPSHOT_JS)
            by_name = {e["name"]: e["ref"] for e in snap["elements"]}

            def describe(ref):
                return page.evaluate(session._DESCRIBE_JS, ref)

            docs = describe(by_name["Docs"])
            assert docs["href"].endswith("/docs") and not docs["inForm"]
            assert describe(by_name["Delete post"])["href"].endswith("#")
            assert describe(by_name["Like"])["isButton"] is True
            assert describe(by_name["Like"])["submits"] is False

            search = describe(by_name["Search"])
            assert search["submits"] and search["formMethod"] == "get"
            assert not search["formHasPassword"]

            sign_in = describe(by_name["Sign in"])
            assert sign_in["submits"] and sign_in["formMethod"] == "post"
            assert sign_in["formHasPassword"] and sign_in["formPasswordFilled"]
            # Whether the password holds something, never what.
            assert "hunter2" not in str(sign_in)

            pw_field = [e for e in snap["elements"] if e["role"] == "password"]
            assert describe(pw_field[0]["ref"])["type"] == "password"

            card = [e for e in snap["elements"] if e["role"] == "text"]
            card_info = [describe(e["ref"]) for e in card]
            assert any(i["autocomplete"] == "cc-number" for i in card_info)

            # Enter in the search box presses the form's first submitter.
            page.focus("input[name=q]")
            focused = describe(None)
            assert focused["editable"] and focused["submitter"] == "Search"

            assert describe(9999) is None
        finally:
            browser.close()


_TALL_PAGE = """<!doctype html><html><head><title>Tall</title></head><body>
<button id="top">Top button</button>
<div style="height: 3000px"></div>
<h2>Pricing</h2>
<a href="/plans">See plans</a>
<input id="range" type="range" min="0" max="100" value="0" style="width:300px">
</body></html>"""


@pytest.mark.skipif(not _chromium_installed(), reason="Chromium is not installed")
def test_snapshot_refs_are_stable_and_bound_to_the_document() -> None:
    from playwright.sync_api import sync_playwright

    from backend.modules.browser import session

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content(_TALL_PAGE)
            first = page.evaluate(session._SNAPSHOT_JS)
            # On screen only, by default: the link 3000px down is not listed.
            assert [e["name"] for e in first["elements"]] == ["Top button"]
            assert first["viewport"] == {"width": 800, "height": 600}

            # The whole page: off-screen elements are listed and marked, and the
            # element seen before keeps its ref.
            full = page.evaluate(session._SNAPSHOT_JS, {"fullPage": True})
            by_name = {e["name"]: e for e in full["elements"]}
            assert by_name["Top button"]["ref"] == first["elements"][0]["ref"]
            assert by_name["See plans"]["onScreen"] is False
            assert full["doc"] == first["doc"]

            # A find searches the whole page, headings included, and never
            # renumbers a ref already handed out.
            found = page.evaluate(session._SNAPSHOT_JS, {"query": "plans"})
            assert [e["name"] for e in found["elements"]] == ["See plans"]
            assert found["elements"][0]["ref"] == by_name["See plans"]["ref"]
            heading = page.evaluate(session._SNAPSHOT_JS, {"query": "pricing"})
            assert heading["elements"][0]["role"] == "heading"

            # The limit caps what is returned, not what is counted.
            capped = page.evaluate(session._SNAPSHOT_JS, {"fullPage": True, "limit": 1})
            assert len(capped["elements"]) == 1 and capped["total"] >= 3

            # describe reports the document, so a ref can be checked against it.
            described = page.evaluate(session._DESCRIBE_JS, first["elements"][0]["ref"])
            assert described["doc"] == first["doc"]

            # A new document is a new id, and its refs start over: ref 1 there is
            # not ref 1 here, which is exactly what the tools refuse to confuse.
            page.set_content(_TALL_PAGE.replace("Top button", "Other button"))
            again = page.evaluate(session._SNAPSHOT_JS)
            assert again["doc"] != first["doc"]
            assert again["elements"][0]["ref"] == first["elements"][0]["ref"]
        finally:
            browser.close()


@pytest.mark.skipif(not _chromium_installed(), reason="Chromium is not installed")
def test_pointer_reaches_what_a_ref_cannot() -> None:
    from playwright.sync_api import sync_playwright

    from backend.modules.browser import session

    dispatch = session.BrowserSession._dispatch
    this = object.__new__(session.BrowserSession)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content(_TALL_PAGE)
            # describe takes a viewport point, up to the control under it.
            box = page.locator("#top").bounding_box()
            assert box is not None
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            at = page.evaluate(session._DESCRIBE_JS, {"x": cx, "y": cy})
            assert at["isButton"] and at["name"] == "Top button"

            # A drag moves a slider no ref could set.
            page.locator("#range").scroll_into_view_if_needed()
            r = page.locator("#range").bounding_box()
            assert r is not None
            y = r["y"] + r["height"] / 2
            dispatch(
                this,
                page,
                "pointer",
                {
                    "action": "drag",
                    "x": r["x"] + 2,
                    "y": y,
                    "toX": r["x"] + r["width"] - 2,
                    "toY": y,
                },
            )
            assert int(page.eval_on_selector("#range", "e => e.value")) > 90

            with pytest.raises(ValueError):
                dispatch(this, page, "pointer", {"action": "wiggle", "x": 1, "y": 1})
        finally:
            browser.close()
