"""Received spans → trajectory runs, debounced per trace.

An exporter's `BatchSpanProcessor` flushes every few seconds or 512 spans, so one
agent run arrives as several batches, children before parents. Re-projecting on
every batch would be correct (`ingest_run` replaces a run's steps wholesale, keyed
on the trace id) but wasteful, so a trace is re-projected once it has been quiet
for `DEBOUNCE_S`.

A trace whose true root never arrives — the agent was called from a process that
does not export, or it crashed — would otherwise sit `running` forever. After
`IDLE_FINAL_S` of silence it is projected once more with `final=True`: finished,
and marked `meta.otel.partial` so nobody mistakes it for a clean run.

**Local turns are never projected.** The orchestrator already records its own
turns into trajectories directly; its spans are in `otel_spans` for the trace
view, and projecting them too would file every chat turn twice. The same goes for
spans a user's MCP server sends back under one of our trace ids — they belong to
that turn, not to a new run.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict

from backend.modules.otel import mapping, store

logger = logging.getLogger("otel")

DEBOUNCE_S = 1.0
IDLE_FINAL_S = 120.0
_TICK_S = 0.25
_MAX_LOCAL = 4096

_lock = threading.Lock()
_dirty: dict[str, float] = {}
_open: dict[str, float] = {}
#: Trace ids this node's tracer started, newest last. In memory as well as in the
#: table because a received span can arrive before the local span that owns it
#: has ended (and so before it was written).
_local: OrderedDict[str, None] = OrderedDict()
_task: asyncio.Task[None] | None = None


def register_local(trace_id: str) -> None:
    with _lock:
        _local[trace_id] = None
        _local.move_to_end(trace_id)
        while len(_local) > _MAX_LOCAL:
            _local.popitem(last=False)


def is_local(trace_id: str) -> bool:
    with _lock:
        if trace_id in _local:
            return True
    return store.has_local_spans(trace_id)


def touch(trace_ids: set[str]) -> None:
    """Mark traces as changed. Without a running worker (tests, CLI) the
    projection happens inline, so behaviour does not depend on a lifespan."""
    if not trace_ids:
        return
    if _task is None or _task.done():
        for trace_id in trace_ids:
            materialize(trace_id)
        return
    now = time.monotonic()
    with _lock:
        for trace_id in trace_ids:
            _dirty[trace_id] = now


def materialize(trace_id: str, *, final: bool = False) -> str | None:
    """Project one trace into its run. Returns the run id, or None when the trace
    is local or empty. Never raises: a bad trace must not stop the next one."""
    try:
        if is_local(trace_id):
            return None
        write = mapping.project(store.get_trace(trace_id), final=final)
        if write is None:
            return None
        if write.cost_usd is None and (write.tokens_in or write.tokens_out):
            write.cost_usd = _price(write)
        from backend.modules.trajectories import store as traj_store

        run_id, _ = traj_store.ingest_run(write)
        with _lock:
            if write.status == "running":
                _open[trace_id] = time.monotonic()
            else:
                _open.pop(trace_id, None)
        return run_id
    except Exception:  # noqa: BLE001
        logger.warning("otel: could not project trace %s", trace_id, exc_info=True)
        return None


def _price(write: mapping.TrajectoryWrite) -> float | None:
    """Priced from our table, same resolver the orchestrator uses. A provider we
    have no row for stays None — unmeasured, never free."""
    try:
        from backend.modules.agent import cost
        from backend.modules.agent.providers import Usage

        return cost.resolve(
            Usage(tokens_in=write.tokens_in, tokens_out=write.tokens_out),
            model=write.model,
            provider_kind=write.provider,
        )
    except Exception:  # noqa: BLE001
        return None


async def _worker() -> None:
    while True:
        await asyncio.sleep(_TICK_S)
        now = time.monotonic()
        with _lock:
            due = [t for t, at in _dirty.items() if now - at >= DEBOUNCE_S]
            for t in due:
                _dirty.pop(t, None)
            stale = [
                t
                for t, at in _open.items()
                if now - at >= IDLE_FINAL_S and t not in _dirty
            ]
            for t in stale:
                _open.pop(t, None)
        for trace_id in due:
            await asyncio.to_thread(materialize, trace_id)
        for trace_id in stale:
            await asyncio.to_thread(materialize, trace_id, final=True)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_worker())


async def stop() -> None:
    """Cancel the worker, then project whatever was still waiting — a batch that
    arrived just before shutdown is the one somebody will look for."""
    global _task
    task, _task = _task, None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    with _lock:
        pending = list(_dirty)
        _dirty.clear()
    for trace_id in pending:
        await asyncio.to_thread(materialize, trace_id)


def reset() -> None:
    """Tests: forget process-global state."""
    with _lock:
        _dirty.clear()
        _open.clear()
        _local.clear()
