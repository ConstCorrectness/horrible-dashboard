"""Run-metric fanout, backfill buffers, and durable storage.

Sentinel events (from kernel cells and script/manim runners) land here: each is
rebroadcast on the `training` channel, and `metrics` points are kept in a per-run
ring buffer so a chart pane opened mid-run can backfill (`watch_run`). A tight
training loop can emit far faster than a chart can drink; metric events are
coalesced to at most `MAX_EVENTS_PER_S` per run (drop-intermediate — the buffer
keeps every point, the wire carries the latest).

## Metrics are durable now

They used to exist **only** in the ring buffer above, which meant they died with
the backend process. You could fine-tune for six hours, restart the app, and have
nothing but a checkpoint — no loss curve, nothing to compare the next run against.
Meanwhile localtrack, the module whose entire job is keeping exactly this, had one
producer (the evals sweep) and four default charts (`train/loss`, `eval/loss`,
`eval/accuracy`, `train/learning_rate`) that **nothing had ever written**.

They are Hugging Face `Trainer` key names, and `ht.callback()` emits precisely
those — so wiring the two together fills the default workspace on the first run.

Three decisions about *where* this hook goes:

- **Here, not in `horrible_train`.** The helper's contract is zero dependencies and
  a stdout sentinel protocol; giving it an HTTP client breaks the Kaggle and Colab
  push, which run it on someone else's machine.
- **Not through `report_to`.** That is a Hugging Face field with known integration
  names — an unknown one is a runtime error, not a no-op. `ht.callback()` is
  already additive and installed regardless of `report_to`, which is exactly why it
  is the right carrier.
- **Every point, not the coalesced wire.** The wire drops intermediates at 20/s
  because a chart cannot draw faster; a *store* has no such excuse, and a loss
  curve missing 90% of its points is not the curve.

Storage is best-effort throughout (`RunMirror` swallows): a tracking failure must
never take down a training run.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

from backend.modules.localtrack.mirror import RunMirror
from backend.modules.settings.routes import get_value
from backend.modules.training.stream import broadcast_threadsafe

logger = logging.getLogger(__name__)

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

#: One localtrack run per training run id, opened on the first metric that names it.
#: Lazily rather than on `run_started`, because a run that emits no metric is not
#: worth a row — and script runners emit metrics without ever sending `run_started`.
_mirrors: dict[str, RunMirror] = {}
#: The most recent run per project, so a new one can close its predecessor.
_latest_by_project: dict[str, str] = {}


def _mirror_for(run_id: str, project_id: str) -> RunMirror:
    """The localtrack run mirroring this training run, creating it if needed.

    The localtrack *project* is the training project id, so each project keeps its
    own run history. (The evals sweep hardcodes `'evals'` for everything, which is
    the shape this deliberately does not copy.)
    """
    mirror = _mirrors.get(run_id)
    if mirror is not None:
        return mirror

    # A new run supersedes the previous one for the same project. This is the only
    # end-of-run signal available here: the helper emits no "finished" event, and
    # kernel lifecycle lives in shared notebook_core rather than in this module. A
    # run whose process simply died is therefore left `running` until the next one
    # starts — visible in the pane, and better than guessing from a timeout.
    previous = _latest_by_project.get(project_id)
    if previous and previous != run_id:
        prior = _mirrors.get(previous)
        if prior is not None:
            prior.finish()

    mirror = RunMirror(
        project_id or "training",
        name=run_id,
        config={"source": "training", "projectId": project_id},
        tags=["training"],
    )
    _mirrors[run_id] = mirror
    if project_id:
        _latest_by_project[project_id] = run_id
    return mirror


def _persist(data: dict[str, Any]) -> None:
    """Write one metric event to localtrack. Never raises.

    Called with `_lock` **released**: this touches SQLite, and holding the fanout
    lock across a disk write would make every chart update wait on it.
    """
    values = data.get("values")
    if not isinstance(values, dict) or not values:
        return
    run_id = str(data.get("runId") or "")
    if not run_id:
        return
    try:
        step = int(data.get("step") or 0)
    except (TypeError, ValueError):
        step = 0
    try:
        _mirror_for(run_id, str(data.get("projectId") or "")).log(step, values)
    except Exception:  # noqa: BLE001 — tracking must never fail a training run
        logger.debug("training: could not persist a metric point", exc_info=True)


def finish_all() -> None:
    """Close every open mirror. Called from the app lifespan on shutdown."""
    for mirror in list(_mirrors.values()):
        mirror.finish()
    _mirrors.clear()
    _latest_by_project.clear()


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
    # Store first, and unconditionally: the wire below drops intermediate points
    # under load, and a stored curve missing them is not the curve.
    _persist(data)

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
    _mirrors.clear()
    _latest_by_project.clear()
