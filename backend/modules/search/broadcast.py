"""Live crawl progress on the `crawl` `/ws` channel.

Cloned from `research/broadcast.py`: a process-global broadcaster the crawl task
publishes to, and a per-connection pump each `/ws` connection runs.

`seed` is a full snapshot the frontend upserts by id, so ordering doesn't matter and
a dropped frame self-heals on the next one. `progress` is throttled at the *producer*
(~2 Hz) rather than the consumer — a crawl fires one per page and a fast docs site
would otherwise put hundreds of frames on the shared socket for no added information.

Control is HTTP-only, so there is no inbound handler here and nothing to add to the
`/ws` channel dispatch chain.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

_PROGRESS_MIN_INTERVAL_S = 0.5


class CrawlBroadcaster:
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


crawl_events = CrawlBroadcaster()

_last_progress: dict[str, float] = {}


def publish_seed(seed: dict[str, Any]) -> None:
    crawl_events.publish({"event": "seed", "data": seed})


def publish_progress(stats: dict[str, Any], *, force: bool = False) -> None:
    """Throttled per-seed progress. `force` bypasses it for the final frame, which
    must always land or the UI stalls one update short of done."""
    seed_id = str(stats.get("seed_id") or "")
    now = time.monotonic()
    if not force and now - _last_progress.get(seed_id, 0.0) < _PROGRESS_MIN_INTERVAL_S:
        return
    _last_progress[seed_id] = now
    crawl_events.publish({"event": "progress", "data": stats})


def publish_page(seed_id: str, url: str, status: str) -> None:
    crawl_events.publish(
        {"event": "page", "data": {"seed_id": seed_id, "url": url, "status": status}}
    )


async def push_crawl_events(conn: Any) -> None:
    queue = crawl_events.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(
                {"channel": "crawl", "event": event["event"], "data": event["data"]}
            )
    finally:
        crawl_events.unsubscribe(queue)
