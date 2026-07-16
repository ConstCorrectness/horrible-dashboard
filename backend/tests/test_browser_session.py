"""The real headless-Chromium browser engine (backend/modules/browser/session.py).

Two tiers:

- **Gate + egress** (no Chromium): with `HORRIBLE_ENABLE_SERVER_BROWSER` unset the manager
  reports `disabled` and spawns nothing; the egress `_route` guard aborts requests to
  loopback/private hosts and lets public + `data:` through. These run everywhere.
- **Real engine** (`importorskip('playwright')` + gate on): a session loads a `data:` page,
  streams a JPEG frame, and `content`/`snapshot`/`scrape` return structured data. Skipped
  unless Playwright (the `browser-engine` extra) is installed.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from backend.modules.browser.session import (
    BrowserManager,
    BrowserSession,
    _response_body,
    server_browser_enabled,
)
from backend.modules.telemetry.recorder import recorder


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


def _events(conn: FakeConn, event: str) -> list[dict[str, Any]]:
    return [
        m["data"]
        for m in conn.sent
        if m.get("channel") == "browser" and m.get("event") == event
    ]


# ---- gate + egress (no Chromium) ------------------------------------------


def test_manager_reports_disabled_when_gate_off(monkeypatch) -> None:
    monkeypatch.delenv("HORRIBLE_ENABLE_SERVER_BROWSER", raising=False)
    assert server_browser_enabled() is False
    mgr = BrowserManager()
    conn = FakeConn()

    asyncio.run(
        mgr.handle(conn, {"channel": "browser", "event": "content", "data": {}})
    )

    assert _events(conn, "disabled"), "gate off should emit a disabled notice"
    assert conn not in mgr.sessions, "no Chromium session should be created"


class _FakeRoute:
    def __init__(self) -> None:
        self.action: str | None = None

    def abort(self) -> None:
        self.action = "abort"

    def continue_(self) -> None:
        self.action = "continue"


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1/admin", "abort"),
        ("http://localhost:8000/x", "abort"),
        ("http://169.254.169.254/latest/meta-data/", "abort"),
        ("http://10.0.0.5/", "abort"),
        ("file:///etc/passwd", "abort"),
        ("data:text/html,hi", "continue"),
        ("https://example.com/", "continue"),
    ],
)
def test_egress_route_guard(url: str, expected: str) -> None:
    session = BrowserSession(FakeConn())  # type: ignore[arg-type]
    route = _FakeRoute()
    session._route(route, _FakeRequest(url))
    assert route.action == expected, url


# ---- real engine (needs Playwright + Chromium) ----------------------------

playwright = pytest.importorskip("playwright")

_PAGE = (
    "data:text/html,"
    "<title>Hi</title><h1>Hello</h1>"
    "<a href='https://example.com/a'>link one</a>"
    "<a href='https://example.com/b'>link two</a>"
    "<button>Go</button>"
)


@pytest.fixture()
def live_session(monkeypatch):
    monkeypatch.setenv("HORRIBLE_ENABLE_SERVER_BROWSER", "1")
    conn = FakeConn()
    sess = BrowserSession(conn, profile="test")

    async def go():
        await sess.start()
        return sess

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
        if sess._start_error:
            pytest.skip(f"browser engine unavailable: {sess._start_error}")
        yield loop, sess, conn
    finally:
        sess.stop()
        # Give the worker a moment to close Chromium before the loop dies.
        time.sleep(0.2)
        loop.close()


@pytest.mark.timeout(90)
def test_navigate_streams_frame_and_reads_content(live_session) -> None:
    loop, sess, conn = live_session

    async def drive():
        await sess.submit("navigate", {"url": _PAGE})
        content = await sess.submit("content", {})
        snap = await sess.submit("snapshot", {})
        scrape = await sess.submit("scrape", {"selector": "a"})
        # Let an idle tick publish at least one frame.
        deadline = time.monotonic() + 5
        while not _events(conn, "frame") and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        return content, snap, scrape

    content, snap, scrape = loop.run_until_complete(drive())

    assert "Hello" in content["text"]
    # Title comes from extraction (may prefer the <h1>) or falls back to <title>.
    assert content["title"] in ("Hi", "Hello")
    # Snapshot returns interactable elements with refs the agent can act on.
    roles = {e["role"] for e in snap["elements"]}
    assert "link" in roles and "button" in roles
    assert all(isinstance(e["ref"], int) for e in snap["elements"])
    # Scrape pulls structured data by selector.
    assert scrape["count"] == 2
    assert scrape["items"][0]["href"] == "https://example.com/a"
    # A JPEG frame reached the socket.
    frames = _events(conn, "frame")
    assert frames and frames[0]["frame"].startswith("data:image/jpeg;base64,")


@pytest.mark.timeout(90)
def test_manager_handle_correlates_result_by_id(monkeypatch) -> None:
    """The WS protocol the frontend uses: an op carrying an `id` gets a `result`
    event echoing that id (request/reply), and the manager auto-creates the session."""
    monkeypatch.setenv("HORRIBLE_ENABLE_SERVER_BROWSER", "1")
    mgr = BrowserManager()
    conn = FakeConn()

    async def drive():
        await mgr.handle(
            conn,
            {
                "channel": "browser",
                "event": "navigate",
                "data": {"url": _PAGE, "id": 7},
            },
        )
        await mgr.handle(
            conn,
            {"channel": "browser", "event": "content", "data": {"id": 42}},
        )

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(drive())
        sess = mgr.sessions.get(conn)
        if sess is None or sess._start_error:
            pytest.skip("browser engine unavailable")
        results = _events(conn, "result")
        by_id = {r["id"]: r for r in results}
        assert 42 in by_id and by_id[42]["op"] == "content"
        assert "Hello" in by_id[42]["result"]["text"]
    finally:
        mgr.close_all()
        time.sleep(0.2)
        loop.close()


# ---- network observability -------------------------------------------------


def test_route_records_a_reason_for_every_block() -> None:
    """`_route` is the only place a blocked request can be attributed: Chromium's
    `requestfailed` can't tell our abort from a DNS failure. If the reason isn't
    stamped here, a blocked request silently reads as `allowed` in the I/O pane."""
    session = BrowserSession(FakeConn())  # type: ignore[arg-type]

    private = _FakeRequest("http://10.0.0.5/")
    session._route(_FakeRoute(), private)
    assert "egress policy" in session._blocked[private]

    scheme = _FakeRequest("file:///etc/passwd")
    session._route(_FakeRoute(), scheme)
    assert "scheme not allowed" in session._blocked[scheme]

    allowed = _FakeRequest("https://example.com/")
    session._route(_FakeRoute(), allowed)
    assert allowed not in session._blocked, "an allowed request must carry no verdict"


def test_response_body_only_captures_texty_payloads() -> None:
    """Pulling every JPEG and video through the ring buffer would dwarf the traffic
    being observed, so bodies are text/JSON only — and never fatal."""

    class _Resp:
        def __init__(self, ctype: str, body: bytes, length: int | None = None):
            self.headers = {"content-type": ctype}
            if length is not None:
                self.headers["content-length"] = str(length)
            self._body = body

        def body(self) -> bytes:
            return self._body

    assert _response_body(_Resp("application/json", b'{"a":1}')) == b'{"a":1}'
    assert (
        _response_body(_Resp("text/html; charset=utf-8", b"<p>hi</p>")) == b"<p>hi</p>"
    )
    assert _response_body(_Resp("image/jpeg", b"\xff\xd8\xff")) is None
    assert _response_body(_Resp("video/mp4", b"\x00\x00")) is None
    # Oversized text is skipped by its declared length rather than read.
    assert _response_body(_Resp("text/plain", b"x", length=999_999_999)) is None

    class _Broken:
        headers = {"content-type": "text/html"}

        def body(self):
            raise RuntimeError("navigation cancelled")

    assert _response_body(_Broken()) is None, "a body read must never escape"


@pytest.mark.timeout(90)
def test_page_requests_are_recorded_and_streamed(live_session, monkeypatch) -> None:
    """End-to-end: a page load produces `browser` I/O events on the recorder and
    `connections` events on the socket. This also exercises the thread hop —
    `_route`/`_on_response` run on the Playwright worker, while the recorder is
    loop-affine, so a missing `call_soon_threadsafe` would show up here."""
    loop, sess, conn = live_session
    recorder.clear()

    async def drive():
        # A same-document navigation with a subresource: the <img> 404s, which is
        # fine — we're asserting the request was *observed*, not that it succeeded.
        await sess.submit(
            "navigate",
            {
                "url": "data:text/html,<title>N</title><img src='https://example.com/x.png'>"
            },
        )
        await asyncio.sleep(1.5)  # let the subresource settle and the post land

    loop.run_until_complete(drive())

    events = [e for e in recorder.recent() if e.source == "browser"]
    assert events, "a page load must produce browser I/O events"
    assert all(e.verdict in ("allowed", "blocked") for e in events)
    assert any("example.com" in e.target for e in events), (
        "the subresource was not observed"
    )


@pytest.mark.timeout(90)
def test_media_op_harvests_describing_text(live_session) -> None:
    """The media op is the only moment an asset's describing words are available —
    once it's a bare URL in the ingest pipeline, the caption is gone. So the
    figcaption and the nearest heading have to come back attached to the image."""
    loop, sess, _conn = live_session
    page = (
        "data:text/html,<title>M</title><h2>Retry strategies</h2>"
        "<figure><img src='https://example.com/a.png' alt='Backoff chart' width='200' height='200'>"
        "<figcaption>Exponential backoff over time</figcaption></figure>"
        "<img src='https://example.com/spacer.gif' width='1' height='1'>"
    )

    async def drive():
        await sess.submit("navigate", {"url": page})
        return await sess.submit("media", {})

    media = loop.run_until_complete(drive())

    # The 1×1 spacer is dropped; only the real image survives.
    assert len(media["images"]) == 1
    img = media["images"][0]
    assert img["src"] == "https://example.com/a.png"
    assert img["alt"] == "Backoff chart"
    # Caption first, then the nearest heading — most- to least-specific.
    assert img["context"] == ["Exponential backoff over time", "Retry strategies"]
