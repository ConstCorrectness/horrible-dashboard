"""Moves turn-stamped I/O events from the live ring into `telemetry_events`.

## One batched subscriber, not a write inside `record()`

`Recorder.record` is on the hot path of every instrumented HTTP request. A
synchronous sqlite write there would put file I/O in the middle of a chat turn to
serve a debugging feature, which is the wrong trade and the inverse of the rule every
other observer in this repo follows. So this subscribes like any other consumer and
flushes on a size or time bound, whichever comes first.

Batching also fixes `amend` for free: `record` and its later `amend` frequently land
in the same batch, and because the write is keyed by `(boot_id, ev_id)` the pair
collapses into one row without any dedupe logic.

## Drops are counted, not prevented

`Recorder._notify` discards on `QueueFull` with a bare `pass`. The fix is *not* to
add backpressure to `record()` -- that would slow the app down to protect a
postmortem. Instead the queue is generous, a drop is detected by watching `ev_id`
skip, and the count is reported on the telemetry route. A number the user can see
beats silent loss; pretending the table is complete would be worse than either.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from backend.modules.telemetry import store
from backend.modules.telemetry.models import IoEvent
from backend.modules.telemetry.recorder import recorder

logger = logging.getLogger("telemetry")

#: Flush when this many events are waiting...
BATCH_MAX = 200
#: ...or when this long has passed with anything waiting at all.
FLUSH_INTERVAL_S = 1.0
#: Prune every Nth flush. Not every one: retention is two DELETEs over an indexed
#: column and there is no reason to pay for them once a second.
PRUNE_EVERY = 60


class _State:
    """Module-level counters, in an object so `reset()` is one assignment."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.written = 0
        #: Events the ring dropped before we saw them, inferred from gaps in
        #: `ev_id`. Surfaced so "the table is incomplete" is visible rather than
        #: silent. Only counts gaps *we* could have stored, i.e. within one boot.
        self.dropped = 0
        self.last_id: int | None = None
        self.flushes = 0


_state = _State()


def stats() -> dict[str, int]:
    return {
        "written": _state.written,
        "dropped": _state.dropped,
        "stored": store.count(),
    }


def _note_gap(event: IoEvent) -> None:
    if _state.last_id is not None and event.id > _state.last_id + 1:
        _state.dropped += event.id - _state.last_id - 1
    _state.last_id = max(event.id, _state.last_id or 0)


async def _flush(batch: list[IoEvent]) -> None:
    if not batch:
        return
    try:
        written = await asyncio.to_thread(store.persist, batch)
    except Exception:  # noqa: BLE001 - a full disk must not kill the drain
        logger.debug("telemetry: batch dropped", exc_info=True)
        return
    _state.written += written
    _state.flushes += 1
    if written and _state.flushes % PRUNE_EVERY == 0:
        try:
            await asyncio.to_thread(store.prune)
        except Exception:  # noqa: BLE001
            logger.debug("telemetry: prune failed", exc_info=True)


def _drain_queue(queue: asyncio.Queue, batch: list[IoEvent]) -> None:
    """Move everything still queued into the batch, without awaiting.

    The batch is not the whole of what we hold. An event is put on the queue by
    `record()` and only reaches `batch` once this task next runs, so at the moment of
    cancellation the most recent events — the ones from the turn that was still in
    flight — are typically *in the queue and not in the batch*. Flushing the batch
    alone would drop exactly them, and would do it while looking correct.
    """
    while True:
        try:
            event = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        _note_gap(event)
        if event.turn_id:
            batch.append(event)


def _flush_sync(batch: list[IoEvent]) -> None:
    """The shutdown path's flush. See the cancellation handler in `_run`."""
    if not batch:
        return
    try:
        _state.written += store.persist(batch)
    except Exception:  # noqa: BLE001 - nothing useful to do while shutting down
        logger.debug("telemetry: final batch dropped", exc_info=True)


async def _run() -> None:
    queue = recorder.subscribe()
    batch: list[IoEvent] = []
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=FLUSH_INTERVAL_S)
            except asyncio.TimeoutError:
                await _flush(batch)
                batch = []
                continue
            _note_gap(event)
            # Filtered here rather than in `persist`, so the batch bound counts
            # rows we will actually write. Most events belong to no turn at all.
            if event.turn_id:
                batch.append(event)
            if len(batch) >= BATCH_MAX:
                await _flush(batch)
                batch = []
    except asyncio.CancelledError:
        # Shutdown. Flush what is in hand: these are the events of the turn that was
        # very likely still running, which is the one somebody will want.
        #
        # **Synchronously**, not through `_flush`. Every `await` inside a task that
        # is already being cancelled raises `CancelledError` again immediately, so an
        # `await asyncio.to_thread(...)` here never reaches the database — the flush
        # would look implemented and do nothing. A blocking sqlite write of at most
        # `BATCH_MAX` rows is milliseconds, and shutdown is not latency-sensitive.
        _drain_queue(queue, batch)
        _flush_sync(batch)
        raise
    finally:
        recorder.unsubscribe(queue)


def start() -> None:
    if _state.task is not None and not _state.task.done():
        return
    store.init_telemetry_db()
    _state.task = asyncio.create_task(_run())


async def stop() -> None:
    task = _state.task
    _state.task = None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def reset() -> None:
    """Test hook."""
    global _state
    _state = _State()
