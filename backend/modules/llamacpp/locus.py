"""Backend half of the **model-locus bus** — "which part of the model am I looking
at", the way `code/locus.py` is "which code am I looking at".

A model locus is `{modelSha, traceId, layer, position, tokenId}`: a cell in a lens
grid, or a block in the model explorer, or the tensor a `dash.lens` sweep just
printed. Publishing it lets three surfaces that know nothing about each other line
up — click layer 15 in the grid and the explorer reveals `blk.15`'s tensors.

**Outbound only**, and that is the deliberate difference from the code locus. The
`code` channel is bidirectional because two *panes* publish to it and both must
follow the other. Here the browser's own clicks need no round trip — the grid and
the explorer are in the same tab, and a local store notifies them synchronously —
so the only thing that has to cross the socket is a locus set from **this side**:
`dash.lens.focus(...)` from the REPL, or the `lens.*` agent tools. Sending the
browser's clicks up as well would buy nothing and cost a frame on every mouse
move over the grid.

Consequently there is no `lens` entry in `app.py`'s inbound channel ladder, and
adding one later is what it would take to make this bidirectional. The fan-out
task is registered there like the other push-only broadcasters.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The latest model locus set from this side. Read by `dash.lens.locus()`.
_current: dict[str, Any] = {}


class LensBroadcaster:
    """Push-only fan-out to every attached browser (the library broadcaster's shape)."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A browser too slow to drain its queue loses a locus update, not
                # the socket. The locus is a cursor: the next one supersedes it.
                pass


lens_events = LensBroadcaster()


def current_locus() -> dict[str, Any]:
    """A copy of the most recent model locus (empty dict if none set yet)."""
    return dict(_current)


def set_locus(locus: dict[str, Any], source: str = "dash") -> dict[str, Any]:
    """Set the model locus and push it to every browser to follow.

    `source` is stamped rather than defaulted at the caller so a follower can tell
    an agent's focus from a REPL sweep's; there is no client `origin`, because
    nothing on this side is echoing its own click back to itself.
    """
    cleaned = {k: v for k, v in locus.items() if v is not None}
    cleaned["source"] = cleaned.get("source", source)
    _current.clear()
    _current.update(cleaned)
    lens_events.publish({"event": "locus", "data": cleaned})
    return cleaned


async def push_lens_events(conn: Any) -> None:
    queue = lens_events.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(
                {"channel": "lens", "event": event["event"], "data": event["data"]}
            )
    finally:
        lens_events.unsubscribe(queue)
