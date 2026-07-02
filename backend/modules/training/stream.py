"""Fan-out of training events to every connected browser tab.

Project-level events (venv/fetch progress, later: metrics, frames, manim status)
are app-global, not per-pane, so every `/ws` connection subscribes on connect
(mirrors the network module's `subscribe_conn`). Worker threads publish through
`broadcast_threadsafe`, which hops onto the event loop captured at subscribe time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_subscribers: set[Any] = set()
_loop: asyncio.AbstractEventLoop | None = None


def subscribe_conn(conn: Any):
    """Register a WsConnection for training broadcasts; returns the unsubscriber."""
    global _loop
    _loop = asyncio.get_running_loop()
    _subscribers.add(conn)

    def unsubscribe() -> None:
        _subscribers.discard(conn)

    return unsubscribe


def _envelope(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "training", "event": event, "data": data}


async def broadcast(event: str, data: dict[str, Any]) -> None:
    for conn in list(_subscribers):
        try:
            await conn.send_json(_envelope(event, data))
        except Exception:  # noqa: BLE001 — one dead socket can't stop the fanout
            _subscribers.discard(conn)


def broadcast_threadsafe(event: str, data: dict[str, Any]) -> None:
    """Publish from a worker thread (venv installs, fetch, runners)."""
    loop = _loop
    if loop is None or loop.is_closed():
        logger.debug("training broadcast dropped (no loop): %s", event)
        return
    asyncio.run_coroutine_threadsafe(broadcast(event, data), loop)
