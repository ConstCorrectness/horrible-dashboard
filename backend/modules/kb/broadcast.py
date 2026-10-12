"""Live catalog updates on the `kb` `/ws` channel.

Same push-only shape as `library.broadcast` and `docviewer.broadcast`: the shared
`/ws` receive loop owns inbound reads, so this side only ever sends.

Two events. `item` carries one enriched item whenever it changes server-side — most
importantly when a site import files a page, which is how the pane watches a course
fill in. `import` carries an import's progress snapshot. Ingest progress for an
item's text is **not** repeated here: it already streams on the `library` channel,
keyed by `source_id`, and the pane maps it back to the item.
"""

from __future__ import annotations

import asyncio
from typing import Any


class KbBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


kb_events = KbBroadcaster()


def publish_item(item: dict[str, Any]) -> None:
    kb_events.publish({"event": "item", "data": item})


def publish_removed(item_ids: list[str]) -> None:
    kb_events.publish({"event": "removed", "data": {"ids": item_ids}})


def publish_import(progress: dict[str, Any]) -> None:
    kb_events.publish({"event": "import", "data": progress})


async def push_kb_events(conn: Any) -> None:
    queue = kb_events.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(
                {"channel": "kb", "event": event["event"], "data": event["data"]}
            )
    finally:
        kb_events.unsubscribe(queue)
