"""Live crawl progress on the `docviewer` `/ws` channel.

Same shape as `library.broadcast`: a process-global, push-only broadcaster that each
browser connection forwards. Push-only matters here for the same reason it does
there — the shared `/ws` receive loop owns inbound reads and cancels this task on
disconnect, so this side never reads.

A crawl is the one thing in this module that takes minutes, and it runs detached from
the request that started it, so this channel is the only way a pane learns that page
40 of 200 just landed.
"""

from __future__ import annotations

import asyncio
from typing import Any


class DocviewerBroadcaster:
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


docviewer_events = DocviewerBroadcaster()


def publish_progress(progress: dict[str, Any]) -> None:
    """Broadcast a crawl-progress snapshot. The pane replaces its state wholesale,
    so a dropped event costs nothing but latency."""
    docviewer_events.publish({"event": "progress", "data": progress})


def publish_page(page: dict[str, Any]) -> None:
    """Broadcast one page row as it lands, so the tree fills in during the crawl."""
    docviewer_events.publish({"event": "page", "data": page})


def publish_set(doc_set: dict[str, Any]) -> None:
    docviewer_events.publish({"event": "set", "data": doc_set})


async def push_docviewer_events(conn: Any) -> None:
    queue = docviewer_events.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(
                {
                    "channel": "docviewer",
                    "event": event["event"],
                    "data": event["data"],
                }
            )
    finally:
        docviewer_events.unsubscribe(queue)
