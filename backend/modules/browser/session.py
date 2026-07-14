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
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.modules.browser.fetch import UnsafeUrlError, _check_host_public
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

_GATE_ENV = "HORRIBLE_ENABLE_SERVER_BROWSER"
_VIEWPORT = {"width": 1280, "height": 800}
_JPEG_QUALITY = 55
# Idle cadence: re-screenshot even without input so async page updates (SPA renders,
# animations, late-loading images) reach the panel without spamming the socket.
_IDLE_FRAME_INTERVAL_S = 0.6
_NAV_TIMEOUT_MS = 20_000
_SEND_TIMEOUT_S = 5.0

# Ops that return a value to the caller (agent tools). Everything else is a
# human interaction whose only effect is the next frame.
_RESULT_OPS = {"content", "snapshot", "scrape", "screenshot", "eval", "info"}


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
        # Skip re-sending a byte-identical frame (idle ticks on a static page).
        self._last_frame_hash: int | None = None
        # Resolved-host cache for the egress route (getaddrinfo per request is costly).
        self._host_ok: dict[str, bool] = {}

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
                viewport=_VIEWPORT,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context.set_default_timeout(_NAV_TIMEOUT_MS)
            context.route("**/*", self._route)
            page = context.pages[0] if context.pages else context.new_page()
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
        """Egress guard: abort any request whose host resolves to a non-public IP."""
        parts = urlsplit(request.url)
        scheme = parts.scheme
        if scheme in ("data", "blob", "about"):
            route.continue_()
            return
        if scheme not in ("http", "https"):
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
            route.abort()

    def _loop_commands(self, page: Any) -> None:
        """Pump commands; on idle, refresh the frame so async updates surface."""
        while not self._closing:
            try:
                cmd = self._queue.get(timeout=_IDLE_FRAME_INTERVAL_S)
            except queue.Empty:
                self._capture_frame(page)
                continue
            if cmd is None:
                return
            try:
                result = self._dispatch(page, cmd.op, cmd.args)
                cmd.future.set_result(result)
            except Exception as exc:  # noqa: BLE001
                cmd.future.set_exception(exc)
            # Every op (human interaction or agent op) may have changed the view.
            self._capture_frame(page)

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
        if op == "snapshot":
            return page.evaluate(_SNAPSHOT_JS)
        if op == "scrape":
            return self._scrape(page, str(args.get("selector", "")))
        if op == "screenshot":
            return {"frame": self._screenshot_uri(page)}
        if op == "eval":
            if not server_browser_enabled():
                raise RuntimeError("browser engine not enabled")
            return {"result": page.evaluate(str(args.get("js", "")))}
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

    def _capture_frame(self, page: Any) -> None:
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
        if not self._emit_threadsafe(
            "frame", {"frame": uri, "url": page.url, "title": _safe_title(page)}
        ):
            self._closing = True


def _safe_title(page: Any) -> str:
    try:
        return page.title()
    except Exception:  # noqa: BLE001
        return ""


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
