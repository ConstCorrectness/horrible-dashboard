"""The outbox runner: sends approved rows, promotes scheduled ones when their time
comes, retries what failed for a passing reason, and resumes what a restart cut off.

The research runner's pattern: one process-global worker over rows, every step
checkpointed in its row (`senders.py`), exponential backoff, and a resume pass on boot.
The repo has no scheduler, so a ticker started in the app's lifespan (every
`TICK_S`) does two things: promote `scheduled` rows whose `run_at` has passed, and
re-queue `sending` rows whose backoff has run out.

One worker, on purpose: posts to one account should go out in the order they were
approved, and nothing here is busy enough to need more.

Progress goes out on the `scrive` WS channel as `outbox.changed {id, site, page,
target, status, label?, progress?}`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.modules.connectors.providers.social_http import SendError
from backend.modules.scrive import outbox, senders
from backend.modules.ws import broadcast_event

logger = logging.getLogger(__name__)

TICK_S = 30.0
MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 15.0
BACKOFF_CAP_S = 15 * 60.0


async def announce(item: outbox.OutboxItem, **extra: Any) -> None:
    await broadcast_event(
        "scrive",
        "outbox.changed",
        {
            "id": item.id,
            "site": item.site,
            "page": item.page,
            "target": item.target,
            "status": item.status,
            **extra,
        },
    )


class OutboxRunner:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queued: set[str] = set()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        if self._tasks:
            return
        outbox.init()
        unknown = outbox.mark_unknown_on_boot()
        if unknown:
            logger.info(
                "outbox resume: %d in-flight step(s) from before the restart", unknown
            )
        for item_id in outbox.ready():
            self.enqueue(item_id)
        self._tasks = [
            asyncio.create_task(self._worker(), name="scrive-outbox-worker"),
            asyncio.create_task(self._ticker(), name="scrive-outbox-ticker"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []
        # Steps in flight stay `running` in their rows; the next start resolves them.

    def enqueue(self, item_id: str) -> None:
        if item_id in self._queued:
            return
        self._queued.add(item_id)
        self._queue.put_nowait(item_id)

    async def tick(self, now: float | None = None) -> list[str]:
        """Promote due scheduled rows and re-queue rows whose backoff passed."""
        promoted = outbox.due(now)
        for item_id in promoted:
            await announce(outbox.get(item_id))
        for item_id in outbox.ready(now):
            self.enqueue(item_id)
        return promoted

    async def _ticker(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — a bad tick must not stop the next one
                logger.exception("outbox tick failed")
            await asyncio.sleep(TICK_S)

    async def _worker(self) -> None:
        while True:
            item_id = await self._queue.get()
            self._queued.discard(item_id)
            try:
                await self.run(item_id)
            except Exception:  # noqa: BLE001 — one row must not stop the worker
                logger.exception("outbox send %s crashed", item_id)

    async def run(self, item_id: str) -> outbox.OutboxItem | None:
        """Send one row (if it is still `sending` and not backing off)."""
        try:
            item = outbox.get(item_id)
        except FileNotFoundError:
            return None
        if item.status != "sending" or (
            item.next_attempt_at and item.next_attempt_at > time.time()
        ):
            return item
        sender = senders.SENDERS.get(item.target)
        if sender is None:
            item = outbox.fail(item_id, f"no sender for {item.target!r}")
            await announce(item)
            return item

        async def progress(label: str, fraction: float | None) -> None:
            await announce(item, label=label, progress=fraction)

        await announce(item, label="starting")
        try:
            remote_id, url = await sender(senders.Job(item, progress))
        except SendError as exc:
            current = outbox.get(item_id)
            if exc.transient and current.attempts + 1 < MAX_ATTEMPTS:
                wait = exc.retry_after or min(
                    BACKOFF_CAP_S, BACKOFF_BASE_S * 2**current.attempts
                )
                item = outbox.back_off(item_id, str(exc), wait)
            else:
                item = outbox.fail(item_id, str(exc))
            await announce(item)
            return item
        except Exception as exc:  # noqa: BLE001 — a bug or a vanished file: stop, say so
            logger.exception("outbox send %s failed", item_id)
            item = outbox.fail(item_id, f"{type(exc).__name__}: {exc}")
            await announce(item)
            return item
        item = outbox.finish(item_id, remote_id=remote_id, url=url)
        await announce(item)
        return item


runner = OutboxRunner()
