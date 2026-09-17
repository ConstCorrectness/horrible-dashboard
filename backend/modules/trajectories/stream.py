"""Live fan-out of trajectory writes to every connected browser tab.

The pane had no liveness at all: a run that took four minutes appeared, complete,
four minutes after it started. For a module whose subject is *an agent working*,
watching the work happen is most of the value, and a postmortem-only view is the
defect.

## Published from the store, not from the recorder

`store.append_step` is the one chokepoint every source passes — the chat
orchestrator, the evals runner, the flow executor, `delegate`, `agentpedia.fork`,
the SDK's `/ingest`, file import, and (later) a run pulled from a peer. Publishing
from `RunRecorder` instead would have been a smaller diff and would have streamed
exactly one of those, with the other seven silently static.

## Writers are not on the event loop

`append_step` is a plain sync function called from async orchestrator code, from
sync HTTP handlers, and potentially from a worker thread. So `publish` is callable
from anywhere and captures the loop explicitly from the app lifespan — the
`localtrack/stream.py` precedent, and for the same reason: a thread has no running
loop to discover lazily, so the event would vanish with only a debug line.

## Payloads are elided, not sent

A single tool result can be 400 KB, and the pane does not need it until somebody
expands that step. The wire carries sizes; `GET /runs/{id}` carries the bytes.
Streaming them would put a megabyte on the socket for a row three words wide.

Nothing here may raise into a caller. A trajectory writer that can fail a live
chat turn by failing to draw a chart is worse than one that draws nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

CHANNEL = "trajectories"

#: Above this, a step's `args`/`result` are replaced on the wire by their size.
#: The pane fetches the real payload from `GET /runs/{id}` when the row is opened.
STREAM_PAYLOAD_MAX = 2048

_loop: asyncio.AbstractEventLoop | None = None


def init_loop() -> None:
    """Capture the event loop, from the app lifespan."""
    global _loop
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - only if called outside the loop
        logger.debug("trajectories: init_loop called with no running loop")


async def _send(event: str, data: dict[str, Any]) -> None:
    from backend.modules.ws import broadcast_event

    try:
        await broadcast_event(CHANNEL, event, data)
    except Exception:  # noqa: BLE001 - a dead socket must not surface to a writer
        logger.debug("trajectories: broadcast failed", exc_info=True)
    # And to any friend watching this node through a share session. Hung off the
    # same publish so a live watcher sees exactly the events this node's own pane
    # does, from every source. Imported lazily: `fabric` imports the store, and the
    # store imports this module.
    try:
        from backend.modules.trajectories import fabric

        await fabric.forward_live(event, data)
    except Exception:  # noqa: BLE001
        logger.debug("trajectories: live forward failed", exc_info=True)


def publish(event: str, data: dict[str, Any]) -> None:
    """Publish one event from any thread. Never raises."""
    loop = _loop
    if loop is None or loop.is_closed():
        # Not an error: tests, the SDK and CLI usage all write with no app running.
        logger.debug("trajectories: event dropped (no loop): %s", event)
        return
    try:
        asyncio.run_coroutine_threadsafe(_send(event, data), loop)
    except Exception:  # noqa: BLE001
        logger.debug("trajectories: publish failed", exc_info=True)


def _sized(value: Any) -> tuple[Any, int | None]:
    """A payload as the wire should carry it, plus its true size in bytes.

    Returns the value itself when it is small, and `None` plus a byte count when it
    is not. The size is always reported: "this step has a 400 KB result you have not
    fetched" and "this step has no result" are different facts, and a row that
    renders them identically is the reason the payload was worth looking at.
    """
    if value is None:
        return None, None
    try:
        # Measured as JSON because that is what the wire and the store both hold;
        # `len(str(dict))` would report Python's repr, which differs enough on
        # quoting and `None`/`null` to make a displayed size visibly wrong.
        encoded = len(json.dumps(value, default=str))
    except Exception:  # noqa: BLE001 - never let formatting break a turn
        return None, None
    if encoded > STREAM_PAYLOAD_MAX:
        return None, encoded
    return value, encoded


def publish_run(run_id: str) -> None:
    """Announce a run opening, or any change to its header.

    One event shape for both, latest-wins on `id`, so the pane keeps one reducer
    rather than reconciling an `open` against a later `update`.
    """
    from backend.modules.trajectories import store

    try:
        run = store.get_run(run_id, with_steps=False)
    except Exception:  # noqa: BLE001
        logger.debug("trajectories: could not read %s for publish", run_id)
        return
    if run is not None:
        publish("run", run.model_dump(mode="json"))


def publish_step(run_id: str, step: Any, seq: int) -> None:
    """Announce one appended step, with large payloads elided."""
    args, args_bytes = _sized(step.args)
    result, result_bytes = _sized(step.result)
    publish(
        "step",
        {
            "runId": run_id,
            "step": {
                "seq": seq,
                "kind": step.kind,
                "round": step.round,
                "role": step.role,
                "name": step.name,
                "args": args,
                "result": result,
                "args_bytes": args_bytes,
                "result_bytes": result_bytes,
                "ok": step.ok,
                "content": step.content,
                "tokens": step.tokens,
                "duration_ms": step.duration_ms,
                "gated": bool(step.gated),
                "error": step.error,
                "ts": step.ts,
            },
        },
    )


def publish_seal(
    run_id: str,
    *,
    status: str,
    steps: int,
    rounds: int,
    duration_ms: int | None,
) -> None:
    """Announce that a run is sealed — the pane's cue to stop treating it as live."""
    publish(
        "seal",
        {
            "runId": run_id,
            "status": status,
            "steps": steps,
            "rounds": rounds,
            "duration_ms": duration_ms,
        },
    )


def reset() -> None:
    """Test hook."""
    global _loop
    _loop = None
