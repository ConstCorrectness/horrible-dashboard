"""Which browser origins may talk to this backend.

A local backend binds loopback and asks no one who is calling, so the browser's
`Origin` is what keeps an unrelated web page from driving it. For HTTP the browser
enforces that itself: same-origin requests go through, and cross-origin ones need
CORS, which allows only :data:`UI_ORIGINS`. **WebSocket upgrades are not covered by
CORS** — any page can open `ws://127.0.0.1:<port>/ws` — so `/ws` applies the same
rule by hand with :func:`ws_origin_allowed`.

Hosted, the hub dials the instance's `/ws` from Python with no `Origin` (and with
the instance token `backend/hosted.py` requires); the hub checks the browser's
origin itself (`_origin_ok` in `backend/hub/app.py`).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

#: Where the UI is served from, when that is not this backend's own origin: the
#: browser layout's Vite dev server, and the Tauri webview (`tauri://localhost` on
#: macOS/Linux, `http://tauri.localhost` on Windows, including a `custom-protocol`
#: build). The CORS allowlist, and the cross-origin half of the `/ws` check.
UI_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "tauri://localhost",
    "http://tauri.localhost",
)


def _is_literal_host(hostname: str) -> bool:
    """An IP literal or `localhost`: a name a DNS-rebinding page cannot have."""
    if hostname == "localhost":
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def ws_origin_allowed(origin: str | None, host: str | None) -> bool:
    """Whether a WebSocket upgrade with this `Origin` and `Host` may open `/ws`.

    - No `Origin`: not a browser (tests, the CLI and SDK, the hosted hub). A web
      page cannot leave it off.
    - An origin in :data:`UI_ORIGINS`.
    - Same-origin, as HTTP is: the Vite proxy forwards `Host` unchanged, so a UI
      opened at `http://127.0.0.1:<any port>` (the preview's auto port) or on a LAN
      address (`pnpm dev:lan`) matches itself. Only when that host is an IP literal
      or `localhost` — a DNS-rebinding page has the attacker's hostname in both
      headers, and would otherwise match too.

    Anything else is refused, `Origin: null` (sandboxed frames, `file:`) included.
    """
    if origin is None:
        return True
    if origin in UI_ORIGINS:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.hostname or not host:
        return False
    return parts.netloc.lower() == host.lower() and _is_literal_host(parts.hostname)
