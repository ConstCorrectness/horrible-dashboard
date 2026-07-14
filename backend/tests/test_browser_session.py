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
    server_browser_enabled,
)


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
