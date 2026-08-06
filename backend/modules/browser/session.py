"""Real headless-Chromium engine for the browser module's "full mode".

The shipped `browser.view` panel renders pages in a cross-origin `<iframe>` — fine for
quick viewing, but opaque: we can't read the DOM, persist cookies, or let the agent act
on a page. This module drives a **real Playwright/Chromium** on the local backend and
server-renders it to the panel, the same pattern the visualizer/vizdoom use: _render
server-side → stream JPEG frames over `/ws` → relay interactions back_. Because the
backend runs on the user's own machine (never a shared host), a server-side browser fits
the app's trust model, and the server holding the live DOM is what makes it agentic.

Key decisions (see docs/modules/browser.mdx):

- **Sync Playwright on a dedicated worker thread**, not the async API. Windows'
  `SelectorEventLoop` under `uvicorn --reload` can't spawn subprocesses via asyncio
  (see the `windows-reload-breaks-subprocess-spawn` memory); Chromium is a subprocess,
  so we drive it from a plain thread and talk to it over a queue, relaying frames to the
  event loop with `run_coroutine_threadsafe` (mirrors `visualizer/runner.py`).
- **Frames come from CDP `Page.startScreencast`**, not a `page.screenshot()` poll.
  Chromium pushes a frame when the page actually *changes* rather than on our timer,
  encodes it inside the browser process, and carries the viewport metadata
  (`deviceWidth/Height`, `pageScaleFactor`, `scrollOffset`) the panel needs to map a
  click back to page space. Its `screencastFrameAck` handshake is also real
  backpressure: Chromium won't send frame N+1 until we've acked N, so a slow socket
  throttles the producer instead of growing an unbounded queue. The old screenshot
  poll survives as `_poll_frame`, used only when screencast can't be started.

  Note screencast emits **JPEG/PNG stills** — there is no H.264/VP8 stream, so the
  frontend decodes with `ImageDecoder`/`createImageBitmap`, not `VideoDecoder`.
- **Persistent context** under `$HORRIBLE_DATA_DIR/browser/<profile>` → cookies, cache,
  localStorage, IndexedDB and site data survive restarts.
- **Egress policy**: every request is gated through `fetch._check_host_public`, so the
  backend Chromium can't be steered at the user's own LAN / cloud-metadata.
- **Gated** behind `HORRIBLE_ENABLE_SERVER_BROWSER=1` — off, the panel stays iframe-only.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import queue
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from backend.modules.browser.cdp import connection_info
from backend.modules.browser.fetch import UnsafeUrlError, _check_host_public
from backend.modules.telemetry.instrument import record_browser_request
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

_GATE_ENV = "HORRIBLE_ENABLE_SERVER_BROWSER"
_DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
_JPEG_QUALITY = 55

# Bounds for a client-requested viewport. The frontend derives these from a pane's
# `getBoundingClientRect()`, so they are untrusted input: a degenerate or enormous
# size would either crash the renderer or make Chromium allocate a huge surface.
_MIN_VIEWPORT = 200
_MAX_VIEWPORT = 4096

# Idle cadence for the **fallback** screenshot poll (screencast unavailable):
# re-screenshot even without input so async page updates reach the panel.
_IDLE_FRAME_INTERVAL_S = 0.6

# Screencast pump. Sync Playwright dispatches CDP events only while the greenlet is
# inside a Playwright call — and the command loop's `queue.get()` is not one, so an
# idle page would never deliver a frame. `_pump` spends this long inside
# `wait_for_timeout`, which yields to the dispatcher; the queue wait before it is
# short so a real command still preempts promptly. Net effect: frames surface within
# ~one pump of Chromium producing them, at ~20 driver round-trips/sec.
_PUMP_MS = 30
_QUEUE_WAIT_S = 0.005
# How often the pump re-reads `page.title()` (a driver round trip) for the frame
# handler's cache. A title changes once per navigation; the pump runs ~20×/sec.
_TITLE_REFRESH_S = 0.5

_NAV_TIMEOUT_MS = 20_000
_SEND_TIMEOUT_S = 5.0

# Ops that return a value to the caller (agent tools). Everything else is a
# human interaction whose only effect is the next frame.
_RESULT_OPS = {
    "content",
    "snapshot",
    "scrape",
    "screenshot",
    "eval",
    "info",
    "media",
    "resize",
}

# Network observability caps. A page can fire hundreds of requests; `_MAX_INFLIGHT`
# bounds the in-flight map against a request that never completes, and
# `_MAX_BODY_BYTES` bounds what any single response contributes to the ring buffer.
_MAX_INFLIGHT = 200
_MAX_BODY_BYTES = 262_144  # 256 KB
_TEXTY_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/x-www-form-urlencoded",
    "+json",
    "+xml",
)


def server_browser_enabled() -> bool:
    """True when the operator has opted into the real backend browser engine."""
    return os.environ.get(_GATE_ENV) == "1"


def _profile_dir(profile: str) -> Path:
    safe = "".join(c for c in profile if c.isalnum() or c in ("-", "_")) or "default"
    root = Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "browser" / safe
    root.mkdir(parents=True, exist_ok=True)
    return root


# JS injected to tag interactable elements with a stable ref and return a flat,
# agent-friendly snapshot (role + accessible name + value). Refs survive as long as
# the DOM node does, and `click_ref`/`type_ref` re-select via the attribute — the
# standard agentic-browser affordance, but with clickable handles the raw a11y tree
# from `page.accessibility.snapshot()` doesn't give.
_SNAPSHOT_JS = r"""
() => {
  const SEL = 'a[href], button, input, select, textarea, [role=button],' +
    '[role=link], [role=tab], [role=menuitem], [role=checkbox], [onclick],' +
    '[contenteditable=true]';
  const out = [];
  let ref = 0;
  const nodes = document.querySelectorAll(SEL);
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0 &&
      rect.bottom > 0 && rect.right > 0 &&
      rect.top < innerHeight && rect.left < innerWidth &&
      getComputedStyle(el).visibility !== 'hidden' &&
      getComputedStyle(el).display !== 'none';
    if (!visible) continue;
    ref += 1;
    el.setAttribute('data-agent-ref', String(ref));
    const role = el.getAttribute('role') ||
      (el.tagName === 'A' ? 'link' :
       el.tagName === 'BUTTON' ? 'button' :
       el.tagName === 'INPUT' ? (el.type || 'textbox') :
       el.tagName.toLowerCase());
    let name = (el.getAttribute('aria-label') || el.innerText ||
      el.value || el.getAttribute('placeholder') ||
      el.getAttribute('title') || el.getAttribute('alt') || '').trim();
    if (name.length > 120) name = name.slice(0, 117) + '...';
    out.push({
      ref, role, name,
      value: (el.value !== undefined ? String(el.value).slice(0, 120) : ''),
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
    });
    if (ref >= 200) break;
  }
  return { url: location.href, title: document.title, elements: out };
}
"""


# JS that harvests the page's media along with the **text that describes it**.
#
# This is the whole basis of media search in the library: there is no multimodal
# embedder in the app (embeddings are text-only — see database/embeddings.py), so an
# image becomes searchable via the words around it — alt text, figcaption, aria-label,
# title, and the nearest heading. Collecting that context here, while the live DOM is
# in hand, is the only moment it's cheaply available; by the time an asset reaches the
# ingest pipeline it's just bytes at a URL. A future CLIP vector would *supplement*
# these fields, not replace them.
_MEDIA_JS = r"""
() => {
  const abs = (u) => { try { return new URL(u, location.href).href; } catch { return null; } };
  const clip = (s, n) => { s = (s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

  // The words that describe an element: its own labels, then its <figure>
  // caption, then the nearest preceding heading. Ordered most- to least-specific.
  const describe = (el) => {
    const parts = [];
    const fig = el.closest('figure');
    const cap = fig && fig.querySelector('figcaption');
    if (cap) parts.push(clip(cap.innerText, 300));
    let node = el, heading = null;
    while (node && !heading) {
      let sib = node.previousElementSibling;
      while (sib && !heading) {
        if (/^H[1-6]$/.test(sib.tagName)) heading = sib;
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    if (heading) parts.push(clip(heading.innerText, 160));
    return parts;
  };

  const images = [];
  for (const el of document.querySelectorAll('img')) {
    const src = abs(el.currentSrc || el.src);
    if (!src || src.startsWith('data:')) continue;      // inline pixels aren't addressable
    const w = el.naturalWidth || el.width, h = el.naturalHeight || el.height;
    if (w && h && w < 64 && h < 64) continue;           // spacers, icons, tracking pixels
    images.push({
      src, kind: 'image',
      alt: clip(el.alt, 300),
      title: clip(el.getAttribute('title'), 160),
      width: w || null, height: h || null,
      context: describe(el),
    });
    if (images.length >= 100) break;
  }

  const videos = [];
  for (const el of document.querySelectorAll('video, iframe')) {
    let src = null, kind = 'video';
    if (el.tagName === 'VIDEO') {
      src = abs(el.currentSrc || el.src);
      if (!src) { const s = el.querySelector('source'); if (s) src = abs(s.src); }
    } else {
      // Only embeds that are actually video players — a generic iframe isn't media.
      const u = abs(el.src) || '';
      if (!/(youtube|youtube-nocookie|vimeo|dailymotion|player\.twitch)\./.test(u)) continue;
      src = u; kind = 'embed';
    }
    if (!src) continue;
    videos.push({
      src, kind,
      alt: clip(el.getAttribute('aria-label') || el.getAttribute('title'), 300),
      title: clip(el.getAttribute('title'), 160),
      width: el.videoWidth || el.width || null,
      height: el.videoHeight || el.height || null,
      duration: (el.duration && isFinite(el.duration)) ? Math.round(el.duration) : null,
      poster: el.poster ? abs(el.poster) : null,
      context: describe(el),
    });
    if (videos.length >= 50) break;
  }

  return { url: location.href, title: document.title, images, videos };
}
"""


class _Cmd:
    """A unit of work handed to the worker thread with a Future for its result."""

    __slots__ = ("op", "args", "future")

    def __init__(self, op: str, args: dict[str, Any]):
        self.op = op
        self.args = args
        self.future: concurrent.futures.Future[Any] = concurrent.futures.Future()


class BrowserSession:
    """One headless-Chromium context, driven from a worker thread, streamed to one WS.

    The worker owns all Playwright objects (they're thread-affine in the sync API);
    the event loop only ever posts `_Cmd`s onto `_queue` and reads results back through
    each command's Future. Frames are pushed the other way via `run_coroutine_threadsafe`.
    """

    def __init__(self, ws_conn: WsConnection, profile: str = "default"):
        self.ws_conn = ws_conn
        self.profile = profile
        self._queue: queue.Queue[_Cmd | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing = False
        self._started = threading.Event()
        self._start_error: str | None = None
        # Skip re-sending a byte-identical frame. Only the fallback poll needs this
        # (screencast fires on damage, so a repeat frame is genuinely a new paint).
        self._last_frame_hash: int | None = None
        # --- screencast ---
        # The CDP session driving both network instrumentation and the frame stream.
        self._cdp: Any = None
        # False until `Page.startScreencast` succeeds; while false the command loop
        # falls back to the screenshot poll.
        self._screencast = False
        # Session ids awaiting `Page.screencastFrameAck`. The ack is deliberately
        # *not* sent from inside the frame handler: that handler runs on the
        # Playwright dispatcher greenlet, and re-entering the driver from it can
        # deadlock. `_pump` drains this instead, ~30ms later — well inside the
        # window that keeps Chromium producing frames.
        self._pending_acks: list[int] = []
        # Live viewport, resizable by the client (see the `resize` op).
        self._viewport = dict(_DEFAULT_VIEWPORT)
        # Title/URL cache. The frame handler can't call `page.title()` (a driver
        # round trip, re-entrant from the dispatcher), so the pump refreshes both.
        self._title = ""
        self._page_url = ""
        self._title_at = 0.0
        # Resolved-host cache for the egress route (getaddrinfo per request is costly).
        self._host_ok: dict[str, bool] = {}
        # Network observability (worker-thread state; see the handlers below).
        # `_inflight` is the live open-connections set, keyed by the Playwright
        # Request object; `_blocked` carries the egress verdict from `_route` to
        # `_on_request_failed`, which is the only place it can be attributed.
        self._inflight: dict[Any, dict[str, Any]] = {}
        self._blocked: dict[Any, str] = {}
        self._seq = 0
        # Connection forensics from the DevTools protocol, keyed by URL.
        #
        # CDP and Playwright identify a request differently (a CDP `requestId` vs a
        # Playwright `Request` object) with no join key between them, so the URL is
        # the correlation. Two in-flight requests to the *same* URL will therefore
        # last-write-wins — acceptable for an inspector, and the alternative is
        # driving the whole network stack through CDP and giving up Playwright's
        # request interception, which is what the egress guard is built on.
        self._cdp_conn: dict[str, dict[str, Any]] = {}

    # ---- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Spawn the worker thread and block until Chromium is up (or errored)."""
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._run, name="browser-session", daemon=True
        )
        self._thread.start()
        await asyncio.to_thread(self._started.wait, 60.0)
        if self._start_error:
            await self._emit("error", {"message": self._start_error})

    def stop(self) -> None:
        self._closing = True
        self._queue.put(None)  # sentinel → worker unwinds and closes Chromium

    # ---- event-loop side ---------------------------------------------------

    async def submit(self, op: str, args: dict[str, Any]) -> Any:
        """Post an op to the worker and await its result (raises on worker error)."""
        if self._closing:
            raise RuntimeError("session closing")
        cmd = _Cmd(op, args)
        self._queue.put(cmd)
        return await asyncio.wrap_future(cmd.future)

    async def _emit(self, event: str, data: dict[str, Any]) -> None:
        await self.ws_conn.send_json(
            {"channel": "browser", "event": event, "data": data}
        )

    def _emit_threadsafe(self, event: str, data: dict[str, Any]) -> bool:
        """Relay to the panel from the worker thread; False if the socket is gone."""
        if self._closing or self._loop is None:
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(self._emit(event, data), self._loop)
            fut.result(timeout=_SEND_TIMEOUT_S)
            return True
        except Exception:  # noqa: BLE001 — loop stopped / socket closed / timed out
            return False

    # ---- worker thread -----------------------------------------------------

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            self._start_error = (
                "The browser engine needs Playwright. Install it with "
                "`uv sync --extra browser-engine && uv run playwright install chromium`."
            )
            logger.warning("Playwright import failed: %s", exc)
            self._started.set()
            return

        pw = None
        context = None
        try:
            pw = sync_playwright().start()
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(_profile_dir(self.profile)),
                headless=True,
                viewport=dict(self._viewport),
                args=["--disable-blink-features=AutomationControlled"],
            )
            context.set_default_timeout(_NAV_TIMEOUT_MS)
            context.route("**/*", self._route)
            context.on("request", self._on_request)
            context.on("response", self._on_response)
            context.on("requestfailed", self._on_request_failed)
            page = context.pages[0] if context.pages else context.new_page()
            self._attach_cdp(context, page)
            self._started.set()
            self._loop_commands(page)
        except Exception as exc:  # noqa: BLE001
            self._start_error = self._start_error or f"browser engine failed: {exc}"
            logger.exception("Browser session crashed")
            self._started.set()
            self._emit_threadsafe("error", {"message": str(exc)})
        finally:
            try:
                if context is not None:
                    context.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if pw is not None:
                    pw.stop()
            except Exception:  # noqa: BLE001
                pass

    def _route(self, route: Any, request: Any) -> None:
        """Egress guard: abort any request whose host resolves to a non-public IP.

        Also the **policy** half of the network-observability story: a request the
        guard kills never reaches Chromium's `requestfailed` with a reason we could
        attribute, so the verdict is stamped here (`self._blocked`) and read back by
        `_on_request_failed`. Blocked requests stay visible in the I/O stream — a
        silent abort is exactly the kind of thing this pane exists to expose.
        """
        parts = urlsplit(request.url)
        scheme = parts.scheme
        if scheme in ("data", "blob", "about"):
            route.continue_()
            return
        if scheme not in ("http", "https"):
            self._blocked[request] = f"scheme not allowed: {scheme or '(none)'}"
            route.abort()  # file://, chrome://, etc.
            return
        host = parts.hostname or ""
        ok = self._host_ok.get(host)
        if ok is None:
            try:
                _check_host_public(host)
                ok = True
            except UnsafeUrlError:
                ok = False
            self._host_ok[host] = ok
        if ok:
            route.continue_()
        else:
            self._blocked[request] = f"blocked by egress policy: {host} is not public"
            route.abort()

    # ---- network observability ---------------------------------------------
    #
    # Chromium's own traffic is the app's biggest blind spot: `_route` sees every
    # request but historically only allowed/aborted it. These three handlers turn
    # that into I/O events (recorded on completion, like the httpx seam) plus a live
    # in-flight set for the open-connections view. All of them run on the **worker
    # thread**, so every hand-off to the recorder or the socket is posted onto the
    # loop rather than called directly.

    def _attach_cdp(self, context: Any, page: Any) -> None:
        """Open the CDP session that carries both network detail and the frame stream.

        Chromium already measures DNS/TCP/TLS/TTFB timing, the peer's IP, the
        negotiated HTTP version and the full certificate for every request — the
        Playwright API just doesn't expose it. One CDP session recovers all of it and
        sends no extra packets, and the same session carries the screencast.

        Both halves are best-effort and independent: this is an inspector *and* a
        viewer, and a browser that renders without a waterfall (or falls back to the
        screenshot poll) beats one that fails to start because a CDP call moved.
        """
        try:
            self._cdp = context.new_cdp_session(page)
            self._cdp.send("Network.enable")
            self._cdp.on("Network.responseReceived", self._on_cdp_response)
        except Exception as exc:  # noqa: BLE001
            logger.info("CDP network instrumentation unavailable: %s", exc)
            self._cdp = None
            return
        self._start_screencast()

    # ---- frame stream (CDP screencast) --------------------------------------

    def _start_screencast(self) -> None:
        """Begin (or restart) the screencast at the current viewport size.

        `maxWidth`/`maxHeight` pin the frame to the viewport so Chromium never scales
        it — the panel's coordinate mapping depends on frame pixels and page pixels
        being the same units, and a silently downscaled frame would put every click
        in the wrong place.
        """
        if self._cdp is None:
            return
        try:
            self._cdp.on("Page.screencastFrame", self._on_screencast_frame)
            self._cdp.send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": _JPEG_QUALITY,
                    "maxWidth": int(self._viewport["width"]),
                    "maxHeight": int(self._viewport["height"]),
                    "everyNthFrame": 1,
                },
            )
            self._screencast = True
        except Exception as exc:  # noqa: BLE001 — fall back to the screenshot poll
            logger.info("CDP screencast unavailable, using screenshot poll: %s", exc)
            self._screencast = False

    def _restart_screencast(self) -> None:
        """Re-arm the screencast after a viewport change (maxWidth/Height are fixed
        at start time, so a resize needs a stop/start to stop being letterboxed)."""
        if self._cdp is None or not self._screencast:
            return
        try:
            self._cdp.send("Page.stopScreencast")
        except Exception:  # noqa: BLE001 — already stopped / page gone
            pass
        self._screencast = False
        self._pending_acks.clear()
        self._start_screencast()

    def _on_screencast_frame(self, event: dict[str, Any]) -> None:
        """Relay one screencast frame to the panel and queue its ack.

        Runs on the Playwright dispatcher greenlet, so it does **no** driver I/O:
        `page.url` is a cached attribute (safe), `page.title()` would be a round trip
        (not), and the ack is deferred to `_pump`. The emit itself is fire-and-forget
        onto the event loop — blocking here would stall the dispatcher, and CDP's
        ack handshake is already the backpressure.
        """
        try:
            data = event.get("data")
            if not data:
                return
            session_id = event.get("sessionId")
            if session_id is not None:
                self._pending_acks.append(int(session_id))
            meta = event.get("metadata") or {}
            self._emit_nowait(
                "frame",
                {
                    "frame": "data:image/jpeg;base64," + str(data),
                    "url": self._page_url,
                    "title": self._title,
                    # Page-space geometry for the panel's input mapping. `deviceWidth`
                    # /`deviceHeight` are the CSS-pixel viewport the frame covers —
                    # authoritative, and the reason the panel no longer hardcodes
                    # 1280×800. `scrollOffset` lets a click be resolved against the
                    # document rather than the viewport.
                    "metadata": {
                        "deviceWidth": meta.get("deviceWidth"),
                        "deviceHeight": meta.get("deviceHeight"),
                        "pageScaleFactor": meta.get("pageScaleFactor"),
                        "offsetTop": meta.get("offsetTop"),
                        "scrollOffsetX": meta.get("scrollOffsetX"),
                        "scrollOffsetY": meta.get("scrollOffsetY"),
                        "timestamp": meta.get("timestamp"),
                    },
                },
            )
        except Exception:  # noqa: BLE001 — a bad frame must never kill the stream
            logger.debug("screencast frame handling failed", exc_info=True)

    def _drain_acks(self) -> None:
        """Ack every frame received since the last pump.

        Chromium stops producing until the outstanding frame is acked, so a dropped
        ack silently freezes the stream — hence the blanket except: a failed ack is
        logged, not raised, and the next frame re-arms.
        """
        if self._cdp is None or not self._pending_acks:
            return
        acks, self._pending_acks = self._pending_acks, []
        for session_id in acks:
            try:
                self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:  # noqa: BLE001
                logger.debug("screencast ack failed", exc_info=True)

    def _on_cdp_response(self, event: dict[str, Any]) -> None:
        """Stash one response's connection detail for `_on_response` to pick up.

        Runs on the worker thread. Deliberately does no I/O and touches no loop
        state — Playwright's `response` event fires separately and is what actually
        records the row.
        """
        try:
            response = event.get("response") or {}
            url = str(response.get("url") or "")
            if not url:
                return
            if len(self._cdp_conn) >= _MAX_INFLIGHT * 2:
                # A page that fires thousands of requests must not grow this map
                # without bound. Oldest-first: dicts preserve insertion order.
                self._cdp_conn.pop(next(iter(self._cdp_conn)), None)
            self._cdp_conn[url] = connection_info(response)
        except Exception:  # noqa: BLE001 — telemetry must never break the browser
            logger.debug("CDP response handling failed", exc_info=True)

    def _post(self, fn: Any) -> None:
        """Run `fn` on the event loop from the worker thread, fire-and-forget."""
        if self._closing or self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(fn)
        except RuntimeError:  # loop already closed
            pass

    def _emit_nowait(self, event: str, data: dict[str, Any]) -> None:
        """Like `_emit_threadsafe` but does not wait for the send to land.

        Frames use the blocking variant on purpose (it backpressures the worker
        against a slow socket). Network events must not: a page load fires hundreds
        of them, and stalling the worker 5s each would hold up the browser itself.
        """
        if self._closing or self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._emit(event, data), self._loop)
        except RuntimeError:
            return
        # Retrieve the exception so a dropped send doesn't warn on GC.
        fut.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)

    def _emit_connections(self) -> None:
        now = time.perf_counter()
        conns = [
            {
                "id": meta["seq"],
                "url": meta["url"],
                "method": meta["method"],
                "resourceType": meta["resource_type"],
                "startedAt": meta["wall"],
                "elapsedMs": round((now - meta["started"]) * 1000, 1),
            }
            for meta in self._inflight.values()
        ]
        self._emit_nowait("connections", {"connections": conns})

    def _on_request(self, request: Any) -> None:
        # Key by the Request object, not id(): it keeps the object alive for the
        # lifetime of the entry, so an id can't be recycled onto a later request.
        if len(self._inflight) >= _MAX_INFLIGHT:
            return
        self._seq += 1
        self._inflight[request] = {
            "seq": self._seq,
            "url": request.url,
            "method": request.method,
            "resource_type": _safe_attr(request, "resource_type"),
            "started": time.perf_counter(),
            "wall": time.time(),
        }
        self._emit_connections()

    def _on_response(self, response: Any) -> None:
        request = response.request
        meta = self._inflight.pop(request, None)
        self._blocked.pop(request, None)
        body = _response_body(response)
        # Correlated by URL — see `_cdp_conn`. Missing is normal (CDP unavailable, or
        # a response Playwright saw first); the row is still recorded, just without
        # the connection detail.
        connection = self._cdp_conn.pop(response.url, {})
        self._post(
            partial(
                record_browser_request,
                method=request.method,
                target=response.url,
                resource_type=_safe_attr(request, "resource_type"),
                verdict="allowed",
                status=response.status,
                duration_ms=_elapsed_ms(meta),
                request_headers=_safe_headers(request),
                response_headers=_safe_headers(response),
                response_bytes=len(body) if body is not None else None,
                body=body,
                **connection,
            )
        )
        self._emit_connections()

    def _on_request_failed(self, request: Any) -> None:
        meta = self._inflight.pop(request, None)
        reason = self._blocked.pop(request, None)
        # No policy reason recorded ⇒ Chromium failed it on its own (DNS, reset,
        # cancelled navigation), which is an `allowed` request that didn't land.
        self._post(
            partial(
                record_browser_request,
                method=request.method,
                target=request.url,
                resource_type=_safe_attr(request, "resource_type"),
                verdict="blocked" if reason else "allowed",
                duration_ms=_elapsed_ms(meta),
                request_headers=_safe_headers(request),
                error=reason or _safe_attr(request, "failure") or "request failed",
            )
        )
        self._emit_connections()

    def _loop_commands(self, page: Any) -> None:
        """Pump commands and, between them, drive the frame stream.

        Two shapes depending on how frames are produced:

        - **screencast** — Chromium pushes frames on its own, but sync Playwright
          only dispatches CDP events while the greenlet is inside a driver call. So
          idling means a short queue wait followed by `_pump`, which spends `_PUMP_MS`
          inside `wait_for_timeout` (the dispatch window) and acks what arrived.
        - **fallback poll** — no screencast, so idling means the original
          screenshot-every-`_IDLE_FRAME_INTERVAL_S` behaviour.
        """
        while not self._closing:
            wait = _QUEUE_WAIT_S if self._screencast else _IDLE_FRAME_INTERVAL_S
            try:
                cmd = self._queue.get(timeout=wait)
            except queue.Empty:
                if self._screencast:
                    self._pump(page)
                else:
                    self._poll_frame(page)
                continue
            if cmd is None:
                return
            try:
                result = self._dispatch(page, cmd.op, cmd.args)
                cmd.future.set_result(result)
            except Exception as exc:  # noqa: BLE001
                cmd.future.set_exception(exc)
            # Every op (human interaction or agent op) may have changed the view.
            # Under screencast Chromium will push the resulting paint by itself; the
            # pump only has to give the dispatcher a window to deliver it.
            if self._screencast:
                self._pump(page)
            else:
                self._poll_frame(page)

    def _pump(self, page: Any) -> None:
        """Give the CDP dispatcher a window to deliver frames, then ack them.

        `wait_for_timeout` is the cheapest driver call that yields to the dispatcher.
        Ordering matters: acks are drained *after* the wait, so frames delivered
        during this window are acked in this pass rather than one pump later.
        """
        try:
            page.wait_for_timeout(_PUMP_MS)
            self._page_url = page.url
        except Exception:  # noqa: BLE001 — page navigating/closing; try next tick
            return
        self._drain_acks()
        self._refresh_title(page)

    def _refresh_title(self, page: Any) -> None:
        """Keep the cached title fresh for the frame handler (which can't ask).

        Throttled: `page.title()` is a driver round trip and the pump runs ~20×/sec,
        but a title changes at most once per navigation. `_TITLE_REFRESH_S` keeps it
        responsive without spending a round trip per frame.
        """
        now = time.monotonic()
        if now - self._title_at < _TITLE_REFRESH_S:
            return
        self._title_at = now
        title = _safe_title(page)
        if title:
            self._title = title

    def _dispatch(self, page: Any, op: str, args: dict[str, Any]) -> Any:
        if op == "navigate":
            return self._navigate(page, str(args.get("url", "")))
        if op == "back":
            page.go_back()
            return None
        if op == "forward":
            page.go_forward()
            return None
        if op == "reload":
            page.reload()
            return None
        if op == "click":
            page.mouse.click(float(args["x"]), float(args["y"]))
            return None
        if op == "scroll":
            page.mouse.wheel(float(args.get("dx", 0)), float(args.get("dy", 0)))
            return None
        if op == "type":
            page.keyboard.insert_text(str(args.get("text", "")))
            return None
        if op == "key":
            page.keyboard.press(str(args.get("key", "")))
            return None
        if op == "click_ref":
            page.click(f'[data-agent-ref="{int(args["ref"])}"]')
            return None
        if op == "type_ref":
            sel = f'[data-agent-ref="{int(args["ref"])}"]'
            page.fill(sel, str(args.get("text", "")))
            return None
        if op == "content":
            return self._content(page)
        if op == "capture":
            return self._capture_page(page)
        if op == "snapshot":
            return page.evaluate(_SNAPSHOT_JS)
        if op == "media":
            return page.evaluate(_MEDIA_JS)
        if op == "scrape":
            return self._scrape(page, str(args.get("selector", "")))
        if op == "screenshot":
            return {"frame": self._screenshot_uri(page)}
        if op == "eval":
            if not server_browser_enabled():
                raise RuntimeError("browser engine not enabled")
            return {"result": page.evaluate(str(args.get("js", "")))}
        if op == "resize":
            return self._resize(page, args.get("width"), args.get("height"))
        if op == "info":
            return {"url": page.url, "title": page.title()}
        raise ValueError(f"unknown browser op: {op}")

    def _navigate(self, page: Any, url: str) -> None:
        url = url.strip()
        if not url:
            return
        # `data:` renders inline with no network — safe, and handy for tests/start pages.
        if url.startswith("data:"):
            page.goto(url, wait_until="domcontentloaded")
            return
        if "://" not in url:
            url = "https://" + url
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise UnsafeUrlError(f"unsupported scheme: {parts.scheme or '(none)'}")
        _check_host_public(parts.hostname or "")
        page.goto(url, wait_until="domcontentloaded")

    def _content(self, page: Any) -> dict[str, Any]:
        from backend.modules.library.extract import extract_article

        html = page.content()
        article = extract_article(html, page.url)
        return {
            "url": page.url,
            "title": article.title or page.title(),
            "author": article.author,
            "text": article.text,
        }

    def _capture_page(self, page: Any) -> dict[str, Any]:
        """Capture the live page as one self-contained HTML artifact.

        `page.content()` serializes the **post-JS** DOM (what the user actually
        sees, SPAs included); subresources are then fetched with the session's own
        cookies via the context's request API and inlined by `research.capture`.
        The artifact is stored server-side — shipping ~25 MB of HTML over `/ws`
        to the frontend and back would buy nothing.
        """
        from backend.modules.artifacts.store import store_bytes
        from backend.modules.library.extract import extract_article
        from backend.modules.research.capture import (
            STYLESHEET_KIND,
            build_page,
            filename_for_title,
            list_css_urls,
            list_resources,
        )

        html = page.content()
        url = page.url
        resources: dict[str, tuple[bytes, str]] = {}
        plan = list_resources(html, url)
        for res_url in plan:
            fetched = self._fetch_subresource(page, res_url)
            if fetched is not None:
                resources[res_url] = fetched
        # One level of indirection: assets referenced by the fetched stylesheets.
        for res_url, kind in plan.items():
            if kind != STYLESHEET_KIND or res_url not in resources:
                continue
            css_text = resources[res_url][0].decode("utf-8", errors="replace")
            for nested in list_css_urls(css_text, res_url):
                if nested not in resources:
                    fetched = self._fetch_subresource(page, nested)
                    if fetched is not None:
                        resources[nested] = fetched

        page_html = build_page(html, url, resources)
        article = extract_article(html, url)
        title = article.title or page.title() or url
        artifact = store_bytes(
            page_html.encode("utf-8"),
            kind="page",
            mime="text/html",
            filename=filename_for_title(title),
            origin_url=url,
            meta={"title": title, "engine": "chromium"},
        )
        return {
            "artifact_id": artifact["id"],
            "url": url,
            "title": title,
            "author": article.author,
            "text": article.text,
        }

    def _fetch_subresource(self, page: Any, url: str) -> tuple[bytes, str] | None:
        """Fetch one capture subresource with the session's cookies, under the same
        egress policy as navigation: scheme + public-host check on **every** redirect
        hop (`max_redirects=0`, manual walk — the context request API doesn't route
        through the page's interception, so it must enforce the policy itself).
        Any failure returns None; the resource simply stays a URL in the archive.
        """
        from backend.modules.research.capture import PER_RESOURCE_CAP

        current = url
        for _ in range(5):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https"):
                return None
            try:
                _check_host_public(parts.hostname or "")
            except UnsafeUrlError:
                return None
            try:
                resp = page.context.request.get(
                    current, max_redirects=0, timeout=10_000
                )
            except Exception:  # noqa: BLE001 — a dead asset must not fail capture
                return None
            if 300 <= resp.status < 400:
                location = resp.headers.get("location")
                if not location:
                    return None
                current = urljoin(current, location)
                continue
            if not resp.ok:
                return None
            body = resp.body()
            if len(body) > PER_RESOURCE_CAP:
                return None
            mime = (resp.headers.get("content-type") or "").split(";")[0].strip()
            return body, mime
        return None

    def _scrape(self, page: Any, selector: str) -> dict[str, Any]:
        if not selector:
            raise ValueError("scrape requires a selector")
        items = page.eval_on_selector_all(
            selector,
            """(els) => els.slice(0, 200).map((el) => ({
                text: (el.innerText || el.textContent || '').trim().slice(0, 500),
                href: el.getAttribute('href') || null,
                html: el.outerHTML.slice(0, 1000),
            }))""",
        )
        return {"selector": selector, "count": len(items), "items": items}

    def _screenshot_uri(self, page: Any) -> str:
        raw = page.screenshot(type="jpeg", quality=_JPEG_QUALITY)
        import base64

        return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")

    def _poll_frame(self, page: Any) -> None:
        """Fallback frame source: screenshot the page and emit it if it changed.

        Only reached when `Page.startScreencast` couldn't be armed (no CDP session,
        or a Chromium that rejected it). Keeps the blocking emit — with no ack
        handshake to throttle the producer, backpressuring the worker against a slow
        socket is the only thing bounding the queue. Frames carry no `metadata`, so
        the panel falls back to the viewport it asked for.
        """
        try:
            raw = page.screenshot(type="jpeg", quality=_JPEG_QUALITY)
        except Exception:  # noqa: BLE001 — mid-navigation / closed page; try next tick
            return
        h = hash(raw)
        if h == self._last_frame_hash:
            return
        self._last_frame_hash = h
        import base64

        uri = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
        self._page_url = page.url
        if not self._emit_threadsafe(
            "frame", {"frame": uri, "url": page.url, "title": _safe_title(page)}
        ):
            self._closing = True

    def _resize(self, page: Any, width: Any, height: Any) -> dict[str, int]:
        """Resize the live viewport to match the pane, and re-arm the screencast.

        The values come from a `getBoundingClientRect()` in the browser, so they are
        clamped rather than trusted — a collapsed pane reports 0, and a mistake
        upstream shouldn't ask Chromium for a 60000px surface.
        """
        w = max(_MIN_VIEWPORT, min(_MAX_VIEWPORT, int(width)))
        h = max(_MIN_VIEWPORT, min(_MAX_VIEWPORT, int(height)))
        if (w, h) == (self._viewport["width"], self._viewport["height"]):
            return {"width": w, "height": h}
        page.set_viewport_size({"width": w, "height": h})
        self._viewport = {"width": w, "height": h}
        # maxWidth/maxHeight are fixed when the screencast starts, so a resize that
        # didn't re-arm would keep delivering frames letterboxed to the old size.
        self._restart_screencast()
        return {"width": w, "height": h}


def _safe_title(page: Any) -> str:
    try:
        return page.title()
    except Exception:  # noqa: BLE001
        return ""


def _safe_attr(obj: Any, name: str) -> str | None:
    """Read a Playwright property that can raise if the object is already torn down."""
    try:
        value = getattr(obj, name)
        return str(value) if value is not None else None
    except Exception:  # noqa: BLE001
        return None


def _safe_headers(obj: Any) -> dict[str, str] | None:
    try:
        return {str(k).lower(): str(v) for k, v in obj.headers.items()}
    except Exception:  # noqa: BLE001
        return None


def _elapsed_ms(meta: dict[str, Any] | None) -> float | None:
    if not meta:
        return None
    return round((time.perf_counter() - meta["started"]) * 1000, 1)


def _response_body(response: Any) -> bytes | None:
    """Body of a *text-ish* response, size-capped.

    Deliberately narrow. Bodies are the most useful thing in the pane and the most
    expensive: pulling a 40 MB video or every JPEG through the ring buffer would
    dwarf the traffic being observed, and `response.body()` on the worker thread
    can raise for a redirect or a cancelled navigation. So: text/JSON only, under
    the cap, and never fatal.
    """
    ctype = ""
    try:
        ctype = response.headers.get("content-type", "")
    except Exception:  # noqa: BLE001
        return None
    if not any(marker in ctype for marker in _TEXTY_CONTENT_TYPES):
        return None
    try:
        if int(response.headers.get("content-length") or 0) > _MAX_BODY_BYTES:
            return None
    except (TypeError, ValueError):
        pass
    try:
        raw = response.body()
    except Exception:  # noqa: BLE001 — redirect, aborted nav, or body already gone
        return None
    return raw[:_MAX_BODY_BYTES] if raw else None


class BrowserManager:
    """Owns one `BrowserSession` per WS connection; tears it down on disconnect.

    Wired in `backend/app.py` alongside `visualizer_manager`: `handle` on the `browser`
    channel, `stop_for` in the disconnect `finally`.
    """

    def __init__(self) -> None:
        self.sessions: dict[WsConnection, BrowserSession] = {}
        self._lock = threading.Lock()

    async def handle(self, ws_conn: WsConnection, message: dict[str, Any]) -> None:
        event = str(message.get("event") or "")
        data = message.get("data") or {}

        if event == "stop":
            self.stop_for(ws_conn)
            return

        if not server_browser_enabled():
            await ws_conn.send_json(
                {
                    "channel": "browser",
                    "event": "disabled",
                    "data": {
                        "message": (
                            "The real browser engine is off. Set "
                            "HORRIBLE_ENABLE_SERVER_BROWSER=1 (and install the "
                            "browser-engine extra) to enable it; the panel stays in "
                            "iframe mode until then."
                        )
                    },
                }
            )
            return

        session = await self._ensure_session(
            ws_conn, str(data.get("profile") or "default")
        )

        if event == "start":
            # Session is up; kick an initial frame + optional first navigation.
            url = data.get("url")
            if url:
                await self._run_op(
                    ws_conn, session, "navigate", {"url": url}, data.get("id")
                )
            return

        await self._run_op(ws_conn, session, event, data, data.get("id"))

    async def _ensure_session(
        self, ws_conn: WsConnection, profile: str
    ) -> BrowserSession:
        existing = self.sessions.get(ws_conn)
        if existing is not None and not existing._closing:
            return existing
        session = BrowserSession(ws_conn, profile)
        self.sessions[ws_conn] = session
        await session.start()
        return session

    async def _run_op(
        self,
        ws_conn: WsConnection,
        session: BrowserSession,
        op: str,
        args: dict[str, Any],
        req_id: Any,
    ) -> None:
        try:
            result = await session.submit(op, args)
        except Exception as exc:  # noqa: BLE001 — surface to the panel/agent, keep socket
            await ws_conn.send_json(
                {
                    "channel": "browser",
                    "event": "error",
                    "data": {"id": req_id, "op": op, "message": str(exc)},
                }
            )
            return
        if op in _RESULT_OPS or req_id is not None:
            await ws_conn.send_json(
                {
                    "channel": "browser",
                    "event": "result",
                    "data": {"id": req_id, "op": op, "result": result},
                }
            )

    async def run_agent_op(
        self, ws_conn: WsConnection, op: str, args: dict[str, Any]
    ) -> Any:
        """Backend entrypoint for agent tools: drive the connection's live session and
        return the op's result directly (used by `agent_tools.py`)."""
        if not server_browser_enabled():
            raise RuntimeError("browser engine not enabled")
        session = await self._ensure_session(
            ws_conn, str(args.get("profile") or "default")
        )
        return await session.submit(op, args)

    def stop_for(self, ws_conn: WsConnection) -> None:
        session = self.sessions.pop(ws_conn, None)
        if session is not None:
            session.stop()

    def close_all(self) -> None:
        for conn in list(self.sessions.keys()):
            self.stop_for(conn)


browser_manager = BrowserManager()
