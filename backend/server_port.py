"""The port this backend is actually serving on — the one authority for it.

Several things hand this node's port to someone else: the peer address baked into
invites and the presence directory (`network.trust.advertised_address`), the tunnel
target for lent extras (`network.lease`), the OAuth redirect back to this backend
(`connectors.oauth`). They used to read `HORRIBLE_DEV_BACKEND_PORT` with a default of
8000 — but only `scripts/dev.mjs` sets that variable. A node started any other way
(the documented bare `uv run uvicorn backend.app:app --port 8100`, a systemd unit)
advertised 8000 wherever it listened. Found live: a friend's node published
`ws://10.0.0.142:8000/peer-ws` while answering on 8100, so every reconnect through
the directory dialed a closed port and failed without a word.

## Why this cannot simply ask the socket at startup

Uvicorn runs the ASGI lifespan startup **before** it binds its listening socket
(`Server.startup`: `lifespan.startup()` first, `create_server` after). The presence
record is published from lifespan, so at that moment there is no socket to ask. Two
sources, in order of authority:

1. **Observed** — the `(host, port)` uvicorn puts in `scope["server"]` of every
   HTTP/WebSocket request, which is `getsockname()` of the socket the request came
   in on. Ground truth, but only available once something has connected. Behind the
   Vite proxy it is still the backend's own socket, never the proxy's.
2. **Configured** — what the server was *told* to bind: `--port`/`--port=` or
   `--bind`/`-b host:port` on the command line, or `UVICORN_PORT` (uvicorn's CLI
   reads `UVICORN_*` env vars). Under `--reload` the worker is a spawned child whose
   `sys.argv` is the parent's, so this holds there too.

Then `HORRIBLE_DEV_BACKEND_PORT`, kept as a fallback, and finally 8000. When the
observed port turns out to differ from what was assumed at startup, `observe`
reports it, so the caller can republish what went out with the wrong one.
"""

from __future__ import annotations

import os
import sys

DEFAULT_PORT = 8000

_observed: int | None = None


def _as_port(value: object) -> int | None:
    try:
        port = int(str(value))
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _from_argv(argv: list[str]) -> int | None:
    """`--port N`, `--port=N`, `--bind host:N`, `-b host:N` — the last one wins,
    the way argparse and click resolve a repeated option."""
    found: int | None = None
    for index, arg in enumerate(argv):
        value: str | None = None
        if arg in ("--port", "--bind", "-b") and index + 1 < len(argv):
            value = argv[index + 1]
        elif arg.startswith(("--port=", "--bind=")):
            value = arg.split("=", 1)[1]
        if value is None:
            continue
        if arg.startswith(("--bind", "-b")):
            # `host:port`; a unix socket (`unix:/path`) has no port to advertise.
            if value.startswith("unix:") or ":" not in value:
                continue
            value = value.rsplit(":", 1)[1]
        port = _as_port(value)
        if port is not None:
            found = port
    return found


def configured_port(
    argv: list[str] | None = None, environ: dict[str, str] | None = None
) -> int | None:
    """The port the server was told to listen on, when that can be read."""
    env = os.environ if environ is None else environ
    from_argv = _from_argv(sys.argv if argv is None else argv)
    if from_argv is not None:
        return from_argv
    return _as_port(env.get("UVICORN_PORT"))


def port() -> int:
    """The port to tell other machines (and our own callbacks) to reach us on."""
    if _observed is not None:
        return _observed
    configured = configured_port()
    if configured is not None:
        return configured
    return _as_port(os.environ.get("HORRIBLE_DEV_BACKEND_PORT")) or DEFAULT_PORT


def observe(server: object) -> bool:
    """Record the port a request actually arrived on. True when that changed what
    `port()` answers — i.e. something may have been advertised with a wrong one.

    `server` is ASGI `scope["server"]`: `(host, port)`, `(path, None)` for a unix
    socket, or absent. Anything without a usable port is ignored.
    """
    global _observed
    if not isinstance(server, (tuple, list)) or len(server) != 2:
        return False
    observed = _as_port(server[1])
    if observed is None or observed == _observed:
        return False
    before = port()
    _observed = observed
    return observed != before


def reset() -> None:
    """Forget the observed port. For tests."""
    global _observed
    _observed = None
