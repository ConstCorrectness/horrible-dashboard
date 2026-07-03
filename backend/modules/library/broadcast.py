"""Live ingestion-status fan-out on the `library` `/ws` channel.

A process-global broadcaster the ingest task publishes source-status snapshots to;
each browser `/ws` connection runs `push_library_events` to forward them. Mirrors
the file-watcher's recorder/stream split (`files` channel). Push-only — the shared
`/ws` receive loop owns inbound reads and cancels this task on disconnect.
"""

from __future__ import annotations

import asyncio
from typing import Any


class LibraryBroadcaster:
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


library_events = LibraryBroadcaster()


def publish_source(source: dict[str, Any]) -> None:
    """Broadcast a full source snapshot; the frontend upserts by id, so event
    ordering relative to the POST response doesn't matter."""
    library_events.publish({"event": "source", "data": source})


async def push_library_events(conn: Any) -> None:
    queue = library_events.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(
                {"channel": "library", "event": event["event"], "data": event["data"]}
            )
    finally:
        library_events.unsubscribe(queue)
