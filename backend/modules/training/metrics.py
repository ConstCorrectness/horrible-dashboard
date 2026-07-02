"""Run-metric fanout and backfill buffers.

Sentinel events (from kernel cells and, later, script/manim runners) land here:
each is rebroadcast on the `training` channel, and `metrics` points are kept in a
per-run ring buffer so a chart pane opened mid-run can backfill (`watch_run`).
A tight training loop can emit far faster than a chart can drink; metric events
are coalesced to at most `MAX_EVENTS_PER_S` per run (drop-intermediate — the
buffer keeps every point, the wire carries the latest).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from backend.modules.settings.routes import get_value
from backend.modules.training.stream import broadcast_threadsafe

MAX_EVENTS_PER_S = 20.0
_MIN_INTERVAL_S = 1.0 / MAX_EVENTS_PER_S


class _RunBuffer:
    def __init__(self, capacity: int) -> None:
        self.points: deque[dict[str, Any]] = deque(maxlen=capacity)
        self.last_sent = 0.0
        self.pending: dict[str, Any] | None = None
        self.flush_timer: threading.Timer | None = None


_buffers: dict[str, _RunBuffer] = {}
_lock = threading.Lock()


def _capacity() -> int:
    try:
        return int(get_value("training.metrics.bufferPoints", 5000))
    except (TypeError, ValueError):
        return 5000


def record_event(ws_event: str, data: dict[str, Any]) -> None:
    """Record + rebroadcast one sentinel event (called from pump threads)."""
    if ws_event != "metrics":
        broadcast_threadsafe(ws_event, data)
        return
    run_id = str(data.get("runId") or data.get("projectId") or "run")
    now = time.monotonic()
    with _lock:
        buf = _buffers.get(run_id)
        if buf is None:
            buf = _buffers[run_id] = _RunBuffer(_capacity())
        buf.points.append(data)
        if now - buf.last_sent < _MIN_INTERVAL_S:
            # Coalesce: latest wins; a timer flushes the tail so the final point
            # of a run always lands even if nothing follows it.
            buf.pending = data
            if buf.flush_timer is None:
                delay = _MIN_INTERVAL_S - (now - buf.last_sent)
                buf.flush_timer = threading.Timer(delay, _flush_run, args=(run_id,))
                buf.flush_timer.daemon = True
                buf.flush_timer.start()
            return
        buf.last_sent = now
        buf.pending = None
    broadcast_threadsafe("metrics", data)


def _flush_run(run_id: str) -> None:
    with _lock:
        buf = _buffers.get(run_id)
        if buf is None:
            return
        buf.flush_timer = None
        point, buf.pending = buf.pending, None
        if point is not None:
            buf.last_sent = time.monotonic()
    if point is not None:
        broadcast_threadsafe("metrics", point)


def backfill(run_id: str) -> list[dict[str, Any]]:
    """Every buffered point for a run (chart pane opened mid-run)."""
    with _lock:
        buf = _buffers.get(run_id)
        return list(buf.points) if buf else []


def known_runs() -> list[str]:
    with _lock:
        return list(_buffers)


def reset() -> None:
    """Test hook."""
    with _lock:
        _buffers.clear()
