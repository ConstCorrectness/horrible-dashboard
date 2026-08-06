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


# ---- screencast + viewport (no Chromium) -----------------------------------


class FakeCdp:
    """Stands in for a Playwright CDP session: records sends, replays handlers."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.handlers: dict[str, Any] = {}
        self.fail_on = fail_on or set()

    def send(self, method: str, params: dict[str, Any] | None = None) -> None:
        if method in self.fail_on:
            raise RuntimeError(f"{method} unavailable")
        self.sent.append((method, params or {}))

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    def methods(self) -> list[str]:
        return [m for m, _ in self.sent]


def _session_with_cdp(
    fail_on: set[str] | None = None,
) -> tuple[BrowserSession, FakeCdp]:
    session = BrowserSession(FakeConn())  # type: ignore[arg-type]
    cdp = FakeCdp(fail_on)
    session._cdp = cdp
    return session, cdp


def test_screencast_starts_pinned_to_the_viewport() -> None:
    """maxWidth/maxHeight must match the viewport exactly.

    If Chromium is allowed to scale the frame, frame pixels and page pixels stop
    being the same units and every relayed click lands somewhere else — silently,
    because a downscaled frame still looks like a valid page.
    """
    session, cdp = _session_with_cdp()
    session._viewport = {"width": 900, "height": 640}

    session._start_screencast()

    assert session._screencast is True
    method, params = cdp.sent[0]
    assert method == "Page.startScreencast"
    assert params["maxWidth"] == 900
    assert params["maxHeight"] == 640
    assert params["format"] == "jpeg"


def test_screencast_failure_falls_back_to_the_poll() -> None:
    """A Chromium that refuses the screencast must still render, via the old poll."""
    session, _cdp = _session_with_cdp(fail_on={"Page.startScreencast"})

    session._start_screencast()

    assert session._screencast is False


def test_frames_are_acked_so_chromium_keeps_producing() -> None:
    """CDP holds frame N+1 until N is acked, so a dropped ack freezes the stream.

    The ack is deliberately not sent from inside the frame handler (that runs on the
    Playwright dispatcher greenlet, where re-entering the driver can deadlock), so
    this pins the two halves: the handler *queues* an ack, the pump *sends* it.
    """
    session, cdp = _session_with_cdp()
    session._start_screencast()

    session._on_screencast_frame(
        {"data": "AAAA", "sessionId": 7, "metadata": {"deviceWidth": 800}}
    )
    assert session._pending_acks == [7], "the handler must not send the ack itself"

    session._drain_acks()
    assert ("Page.screencastFrameAck", {"sessionId": 7}) in cdp.sent
    assert session._pending_acks == [], "a drained ack must not be sent twice"


def test_frame_carries_page_geometry() -> None:
    """The panel scales clicks by the frame's own deviceWidth/Height, so the
    metadata has to survive the relay — without it the panel falls back to a
    guess and every click on a resized pane is off."""
    conn = FakeConn()
    session = BrowserSession(conn)  # type: ignore[arg-type]
    session._cdp = FakeCdp()
    emitted: list[tuple[str, dict[str, Any]]] = []
    session._emit_nowait = lambda event, data: emitted.append((event, data))  # type: ignore[method-assign]

    session._on_screencast_frame(
        {
            "data": "QUJD",
            "sessionId": 1,
            "metadata": {
                "deviceWidth": 1024,
                "deviceHeight": 768,
                "pageScaleFactor": 1,
                "scrollOffsetY": 240,
            },
        }
    )

    event, data = emitted[0]
    assert event == "frame"
    assert data["frame"].startswith("data:image/jpeg;base64,QUJD")
    assert data["metadata"]["deviceWidth"] == 1024
    assert data["metadata"]["deviceHeight"] == 768
    assert data["metadata"]["scrollOffsetY"] == 240


class FakePage:
    def __init__(self) -> None:
        self.viewports: list[dict[str, int]] = []

    def set_viewport_size(self, size: dict[str, int]) -> None:
        self.viewports.append(size)


def test_resize_clamps_untrusted_sizes() -> None:
    """Sizes come from a getBoundingClientRect() in the browser, so they're input,
    not fact: a collapsed pane reports 0 and a bug upstream could ask for a surface
    big enough to take the renderer down."""
    session, _cdp = _session_with_cdp()
    page = FakePage()

    assert session._resize(page, 0, 0) == {"width": 200, "height": 200}
    assert session._resize(page, 99_999, 99_999) == {"width": 4096, "height": 4096}


def test_resize_rearms_the_screencast() -> None:
    """maxWidth/maxHeight are fixed when the screencast starts, so a resize that
    didn't restart it would keep delivering frames letterboxed to the old size."""
    session, cdp = _session_with_cdp()
    session._start_screencast()
    page = FakePage()

    session._resize(page, 640, 480)

    assert page.viewports == [{"width": 640, "height": 480}]
    assert cdp.methods().count("Page.stopScreencast") == 1
    starts = [p for m, p in cdp.sent if m == "Page.startScreencast"]
    assert starts[-1]["maxWidth"] == 640 and starts[-1]["maxHeight"] == 480


def test_resize_to_the_same_size_is_a_no_op() -> None:
    """A ResizeObserver fires on every layout pass; an unchanged size must not cost
    a Chromium relayout and a screencast restart."""
    session, cdp = _session_with_cdp()
    session._start_screencast()
    page = FakePage()

    session._resize(page, 640, 480)
    before = len(cdp.sent)
    session._resize(page, 640, 480)

    assert len(cdp.sent) == before
    assert len(page.viewports) == 1


@pytest.mark.timeout(90)
def test_live_frames_come_from_the_screencast(live_session) -> None:
    """End-to-end proof the screencast is actually the frame source.

    The other live tests only assert that *a* frame arrived, which the fallback
    screenshot poll would satisfy too — so a broken screencast would silently
    degrade to the old behaviour and every test would stay green. The metadata is
    the tell: only CDP frames carry it.
    """
    loop, sess, conn = live_session

    async def drive():
        await sess.submit("navigate", {"url": _PAGE})
        # Let the pump run so queued screencast frames are dispatched and acked.
        await asyncio.sleep(0.5)

    loop.run_until_complete(drive())

    assert sess._screencast is True, "screencast failed to arm; fell back to the poll"
    frames = _events(conn, "frame")
    assert frames, "no frame streamed"
    with_meta = [f for f in frames if f.get("metadata", {}).get("deviceWidth")]
    assert with_meta, f"no frame carried CDP metadata (got {frames[0].keys()})"
    # The viewport the session was launched with, reported back by Chromium itself.
    assert with_meta[-1]["metadata"]["deviceWidth"] == 1280
    assert with_meta[-1]["metadata"]["deviceHeight"] == 800


@pytest.mark.timeout(90)
def test_live_resize_changes_the_reported_viewport(live_session) -> None:
    """A resize must reach Chromium and come back in the frame metadata — that
    round trip is what the panel's click mapping trusts."""
    loop, sess, conn = live_session

    async def drive():
        await sess.submit("navigate", {"url": _PAGE})
        applied = await sess.submit("resize", {"width": 900, "height": 600})
        await asyncio.sleep(0.5)
        return applied

    applied = loop.run_until_complete(drive())
    assert applied == {"width": 900, "height": 600}

    sized = [
        f["metadata"]["deviceWidth"]
        for f in _events(conn, "frame")
        if f.get("metadata", {}).get("deviceWidth")
    ]
    assert 900 in sized, (
        f"no frame reported the new viewport (saw {sorted(set(sized))})"
    )
