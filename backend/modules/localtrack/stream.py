"""Live fan-out of LocalTrack changes to every connected browser tab.

The pane had no liveness at all — no channel, no polling, no interval — so a
sweep or a training run could be writing metrics for ten minutes while the charts
sat still, and the only way to see anything was to hit refresh. For a module whose
entire job is watching a run happen, that is the defect.

Two properties this has to satisfy, and they pull in opposite directions:

- **Writers are not on the event loop.** `ingest_metrics` is called from the evals
  sweep, from HTTP handlers, and (once training reports here) from the metrics
  pump *thread*. So publishing has to be callable from anywhere, which is what
  `publish` is for — the `training/stream.py` precedent.
- **A tight training loop emits far faster than a chart can drink.** So metric
  events are coalesced per run: at most `MAX_EVENTS_PER_S`, latest-wins, with a
  timer flushing the tail so the final point of a run always lands even when
  nothing follows it. Same shape as `training/metrics.py`, and for the same
  reason — the *store* keeps every point, the wire carries the latest.

Nothing here is allowed to raise into a caller. A tracking module that can fail a
training run by failing to draw a chart is worse than one that draws nothing.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

CHANNEL = "localtrack"

MAX_EVENTS_PER_S = 20.0
_MIN_INTERVAL_S = 1.0 / MAX_EVENTS_PER_S

_loop: asyncio.AbstractEventLoop | None = None

_lock = threading.Lock()
_last_sent: dict[str, float] = {}
_pending: dict[str, dict[str, Any]] = {}
_timers: dict[str, threading.Timer] = {}


def init_loop() -> None:
    """Capture the event loop, from the app lifespan.

    Captured explicitly rather than lazily because the first publisher is very
    likely a worker thread (the metrics pump), and a thread has no running loop to
    discover — the event would be dropped with only a debug line to show for it.
    """
    global _loop
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — only if called outside the loop
        logger.debug("localtrack: init_loop called with no running loop")


async def _send(event: str, data: dict[str, Any]) -> None:
    from backend.modules.ws import broadcast_event

    try:
        await broadcast_event(CHANNEL, event, data)
    except Exception:  # noqa: BLE001 — a dead socket must not surface to a writer
        logger.debug("localtrack: broadcast failed", exc_info=True)


def publish(event: str, data: dict[str, Any]) -> None:
    """Publish one event from any thread. Never raises."""
    loop = _loop
    if loop is None or loop.is_closed():
        # Not an error: tests and CLI usage write to the store with no app running.
        logger.debug("localtrack: event dropped (no loop): %s", event)
        return
    try:
        asyncio.run_coroutine_threadsafe(_send(event, data), loop)
    except Exception:  # noqa: BLE001
        logger.debug("localtrack: publish failed", exc_info=True)


def publish_metrics(run_id: str, keys: list[str]) -> None:
    """Announce that a run has new points, coalesced per run.

    Deliberately carries the run and the metric *keys* rather than the values: the
    pane already knows how to fetch a downsampled series (and must, since it
    applies its own LTTB budget and EMA), so shipping raw values here would be a
    second, un-downsampled copy of the data on the wire for no benefit.
    """
    now = time.monotonic()
    payload = {"runId": run_id, "keys": sorted(set(keys))}
    with _lock:
        last = _last_sent.get(run_id, 0.0)
        if now - last < _MIN_INTERVAL_S:
            prev = _pending.get(run_id)
            if prev is not None:
                # Union the keys, don't overwrite: a coalesced burst that touched
                # `train/loss` then `train/grad_norm` must announce both, or a
                # panel for the dropped key never learns it has data.
                payload["keys"] = sorted(set(prev["keys"]) | set(payload["keys"]))
            _pending[run_id] = payload
            if run_id not in _timers:
                timer = threading.Timer(
                    _MIN_INTERVAL_S - (now - last), _flush, args=(run_id,)
                )
                timer.daemon = True
                _timers[run_id] = timer
                timer.start()
            return
        _last_sent[run_id] = now
        _pending.pop(run_id, None)
    publish("metrics", payload)


def _flush(run_id: str) -> None:
    with _lock:
        _timers.pop(run_id, None)
        payload = _pending.pop(run_id, None)
        if payload is not None:
            _last_sent[run_id] = time.monotonic()
    if payload is not None:
        publish("metrics", payload)


def reset() -> None:
    """Test hook."""
    global _loop
    with _lock:
        for timer in _timers.values():
            timer.cancel()
        _timers.clear()
        _pending.clear()
        _last_sent.clear()
    _loop = None
