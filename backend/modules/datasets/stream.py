"""Fan-out of dataset build events to every connected browser tab.

A build is a long detached job and its progress belongs to the node, not to the
tab that started it: close the builder pane mid-build and the run must still be
watchable from anywhere, which is why every `/ws` connection subscribes on connect
rather than the pane subscribing per instance (the `training` and `library`
precedent). Worker threads publish through `broadcast_threadsafe`, which hops onto
the event loop captured at subscribe time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_subscribers: set[Any] = set()
_loop: asyncio.AbstractEventLoop | None = None


def subscribe_datasets_conn(conn: Any):
    """Register a WsConnection for datasets broadcasts; returns the unsubscriber."""
    global _loop
    _loop = asyncio.get_running_loop()
    _subscribers.add(conn)

    def unsubscribe() -> None:
        _subscribers.discard(conn)

    return unsubscribe


def _envelope(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "datasets", "event": event, "data": data}


async def broadcast(event: str, data: dict[str, Any]) -> None:
    for conn in list(_subscribers):
        try:
            await conn.send_json(_envelope(event, data))
        except Exception:  # noqa: BLE001 — one dead socket can't stop the fanout
            _subscribers.discard(conn)


def broadcast_threadsafe(event: str, data: dict[str, Any]) -> None:
    """Publish from a worker thread (the build worker)."""
    loop = _loop
    if loop is None or loop.is_closed():
        logger.debug("datasets broadcast dropped (no loop): %s", event)
        return
    asyncio.run_coroutine_threadsafe(broadcast(event, data), loop)
