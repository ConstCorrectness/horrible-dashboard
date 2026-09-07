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

#: run id -> the name the helper announced, from `run_started`. Needed because a
#: script mints its own run id and the sweep that launched it cannot know it in
#: advance — the *name* is the only thing both sides agree on beforehand.
_names: dict[str, str] = {}

#: (project id, run name) -> the config that run is exercising. A sweep declares
#: one per point before launching it.
#:
#: This is the whole mechanism behind "which knob caused this". localtrack has
#: stored `config_json` per run since it was written and never had a producer that
#: put anything comparable in it; the comparison view reads it back and diffs.
_declared: dict[tuple[str, str], dict[str, Any]] = {}


def declare_run_config(project_id: str, run_name: str, config: dict[str, Any]) -> None:
    """Say what a not-yet-started run will be exercising, keyed by its name."""
    _declared[(project_id, run_name)] = dict(config)


def _mirror_for(run_id: str, project_id: str) -> RunMirror:
    """The localtrack run mirroring this training run, creating it if needed.

    The localtrack *project* is the training project id, so each project keeps its
    own run history. (The evals sweep hardcodes `'evals'` for everything, which is
    the shape this deliberately does not copy.)
    """
    mirror = _mirrors.get(run_id)
    if mirror is not None:
        return mirror

    # A new run supersedes the previous one for the same project. This is the
    # *backstop* end-of-run signal, not the primary one: `ht.finish()` (helper) and
    # a script runner's process exit both call `finish_run` below. This branch only
    # catches a run whose process died without either — a killed kernel, a hard
    # crash — and leaves it `running` until the next one starts rather than
    # guessing from a timeout.
    previous = _latest_by_project.get(project_id)
    if previous and previous != run_id:
        prior = _mirrors.get(previous)
        if prior is not None:
            prior.finish()

    name = _names.get(run_id) or run_id
    declared = _declared.get((project_id, name), {})
    mirror = RunMirror(
        project_id or "training",
        # The helper's name, not the hex id: a sweep names each point after what
        # varies (`learning_rate=0.0002`), and a sidebar of twelve hex ids is a
        # sidebar you cannot read.
        name=name,
        config={"source": "training", "projectId": project_id, **declared},
        tags=["training", "sweep"] if declared else ["training"],
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


#: Terminal statuses localtrack understands. Anything else is coerced to `failed`
#: rather than written through — an unknown status renders as no status at all.
TERMINAL = ("finished", "failed", "crashed")


def finish_run(
    run_id: str, status: str = "finished", summary: dict[str, Any] | None = None
) -> bool:
    """Close one run with a terminal status. Returns False if it was already closed.

    Idempotent on purpose: a generated recipe emits `ht.finish()` from
    `on_train_end` *and* the script runner closes the run when the process exits,
    so the common case is two calls for one run. The second must be a no-op, not a
    second row and not an overwrite of the real status with the process's.
    """
    if not run_id:
        return False
    mirror = _mirrors.pop(run_id, None)
    _names.pop(run_id, None)
    if mirror is None:
        return False
    for project_id, latest in list(_latest_by_project.items()):
        if latest == run_id:
            _latest_by_project.pop(project_id, None)
    mirror.finish(
        status if status in TERMINAL else "failed",
        summary=summary or {},
    )
    return True


def finish_all() -> None:
    """Close every open mirror. Called from the app lifespan on shutdown."""
    for mirror in list(_mirrors.values()):
        mirror.finish()
    _mirrors.clear()
    _latest_by_project.clear()
    _names.clear()
    # `_declared` is deliberately NOT cleared here: shutdown closes runs, and a
    # sweep's declared configs belong to points that may not have started yet.


def _capacity() -> int:
    try:
        return int(get_value("training.metrics.bufferPoints", 5000))
    except (TypeError, ValueError):
        return 5000


def record_event(ws_event: str, data: dict[str, Any]) -> None:
    """Record + rebroadcast one sentinel event (called from pump threads)."""
    if ws_event == "run_started":
        # Remember the name before any metric arrives: `_mirror_for` runs on the
        # first metric point and needs it to look up the declared config.
        run_id = str(data.get("runId") or "")
        if run_id:
            _names[run_id] = str(data.get("name") or run_id)
        broadcast_threadsafe(ws_event, data)
        return
    if ws_event == "run_finished":
        # Close the store row *before* the wire, so a pane that reacts to the
        # broadcast by re-reading the run sees the terminal status rather than
        # racing the write and showing `running` one last time.
        summary = data.get("summary")
        finish_run(
            str(data.get("runId") or ""),
            str(data.get("status") or "finished"),
            summary if isinstance(summary, dict) else {},
        )
        broadcast_threadsafe(ws_event, data)
        return
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
    _names.clear()
    _declared.clear()
