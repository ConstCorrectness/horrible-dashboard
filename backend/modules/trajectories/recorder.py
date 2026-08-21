"""Live capture of this node's own agent runs.

One seam: `run_agent_loop` in `agent/orchestrator.py`. All four internal callers —
the chat orchestrator, `delegate.run_delegate`, the flow executor's Agent node, and
the evals runner — go through that function, so hooking it once captures every
internal source and there is no second place to keep in sync.

## Observation must not break the thing it observes

Every entry point here swallows and logs. A trajectory writer that raised would
take down a live chat turn, which is a catastrophic trade for a debugging feature.
This is the same rule `interpretability/recorder.py` follows, for the same reason.

## Off is the default, and off must be free

`begin()` returns `None` unless some dataset has `capture=1`, and it asks that
question with a single indexed lookup. A node that never turns capture on pays one
SELECT per agent turn.

## What this deliberately does not record

Token counts and the per-round context blocks. Those are interpretability's
`agent_turns`, keyed by the same `turn_id` — that table holds what the model was
*shown*, this one holds what it *did*, and duplicating the first into the second
would mean two copies of a 4 KB prompt drifting apart. The join is the design.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.trajectories import store
from backend.modules.trajectories.models import HarnessWrite, StepWrite

logger = logging.getLogger("trajectories")

#: Strong references to in-flight indexing tasks. `asyncio` only keeps a weak
#: reference, so a task nobody holds can be collected before it runs.
_pending_index: set[Any] = set()


def _goal_from(messages: list[dict[str, Any]]) -> str:
    """The task, as one line: the last user message.

    The *last*, not the first — a follow-up turn carries the whole history, and
    the first user message would label every turn in a long conversation with the
    thing that was asked an hour ago.
    """
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()[:500]
    return ""


def _system_prompt_from(messages: list[dict[str, Any]]) -> str:
    """Every system message, joined.

    The runner assembles several (the agent prompt, the skills catalog, the group
    guides) and all of them shape behaviour, so all of them belong in the
    fingerprint. Joining rather than taking the first is the difference between a
    fingerprint that changes when the skills catalog changes and one that does not.
    """
    parts = [
        str(m.get("content") or "")
        for m in messages
        if m.get("role") == "system" and m.get("content")
    ]
    return "\n\n".join(parts)


def _setting(key: str, default: Any) -> Any:
    """Read a user setting, falling back to the manifest default.

    Settings are declared frontend-side and only *overrides* are persisted, so the
    default has to be repeated here. Keep it in step with the declaration in
    `packages/core/src/modules/trajectories/index.tsx`.
    """
    try:
        from backend.modules.settings.routes import _read

        value = _read().get(key)
        return default if value is None else value
    except Exception:  # pragma: no cover
        return default


def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for tool in tools:
        name = (tool.get("function") or {}).get("name")
        if name:
            names.append(str(name))
    return names


class RunRecorder:
    """A handle on one in-flight run. Cheap, and never raises at a caller."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.rounds = 0
        self._seq = 0
        self._failed: str = ""

    def _next(self) -> int:
        seq = self._seq
        self._seq += 1
        return seq

    def message(self, round_no: int, content: str, role: str = "assistant") -> None:
        if not content:
            return
        self._record(
            StepWrite(
                kind="message",
                role=role,
                round=round_no,
                content=content,
                seq=self._next(),
            )
        )

    def action(
        self,
        round_no: int,
        name: str,
        args: dict[str, Any],
        result: Any,
        *,
        duration_ms: int | None = None,
    ) -> None:
        """One tool call and the result it returned — a single step.

        `ok` and `gated` are derived here rather than passed in, because the
        orchestrator represents both as an `error` key on the result dict and the
        distinction between "the tool failed" and "the harness blocked it" is one
        of the more useful things a trajectory can tell you.
        """
        error = None
        gated = False
        if isinstance(result, dict) and result.get("error"):
            error = str(result["error"])
            gated = "denied by permission policy" in error
        self._record(
            StepWrite(
                kind="action",
                round=round_no,
                name=name,
                args=args,
                result=result,
                ok=error is None,
                error=error,
                gated=gated,
                duration_ms=duration_ms,
                seq=self._next(),
            )
        )

    def _record(self, step: StepWrite) -> None:
        try:
            store.append_step(self.run_id, step)
        except Exception as exc:  # pragma: no cover - never cost the user a turn
            logger.debug("trajectories: step dropped for %s: %s", self.run_id, exc)

    def fail(self, exc: BaseException) -> None:
        self._failed = f"{type(exc).__name__}: {exc}"

    def finish(self, answer: str) -> None:
        try:
            if answer and not self._failed:
                store.append_step(
                    self.run_id,
                    StepWrite(
                        kind="message",
                        role="assistant",
                        round=self.rounds,
                        content=answer,
                        seq=self._next(),
                    ),
                )
            store.finish_run(
                self.run_id,
                status="failed" if self._failed else "complete",
                rounds=self.rounds or None,
                error=self._failed,
            )
            self._schedule_index()
        except Exception as exc:  # pragma: no cover
            logger.debug("trajectories: could not seal %s: %s", self.run_id, exc)

    def _schedule_index(self) -> None:
        """Add this run to the vector index, off the turn's critical path.

        Detached rather than awaited: embedding is a network round-trip and
        `merge_insert` is a whole-table write (~1.5s), and the user is waiting on
        their answer. If there is no running loop — a test calling `finish`
        directly — this does nothing, and `POST /reindex` picks the run up later.
        """
        try:
            import asyncio

            from backend.modules.trajectories import search

            loop = asyncio.get_running_loop()
            task = loop.create_task(search.index_runs([self.run_id]))
            # Held so the task is not garbage-collected mid-flight, and discarded
            # on completion so the set does not grow for the process's lifetime.
            _pending_index.add(task)
            task.add_done_callback(_pending_index.discard)
        except RuntimeError:
            pass  # no running loop; reindex will catch it
        except Exception as exc:  # pragma: no cover
            logger.debug("trajectories: could not schedule indexing: %s", exc)


def _provenance(conn: Any) -> tuple[str, str]:
    """`(source, node_id)` for the run, read off the connection.

    A turn a peer asked for runs on *this* node through the ordinary orchestrator
    loop (`network/agent_bridge.handle_remote_agent_request`), so it is already
    being captured — what it lacks is any sign of whose request it was. Recording
    it as `local` would put another node's questions in your own dataset with no
    way to tell them apart, which quietly corrupts every per-harness aggregate.

    Read defensively: `conn` is a browser socket, an `EvalConnection`, or a
    `RemoteAgentConn`, and only the last has these attributes.
    """
    if getattr(conn, "is_remote", False):
        return "peer", str(getattr(conn, "dst", "") or "")
    return "local", ""


def begin(
    *,
    turn_id: str,
    parent_turn_id: str | None,
    agent_id: str,
    agent_name: str,
    model: str,
    provider: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    params: dict[str, Any],
    conn: Any = None,
) -> RunRecorder | None:
    """Open a run if capture is on, else return None.

    The harness is fingerprinted from the catalog **as it stands at entry**. Under
    progressive disclosure the tool list is recomputed every round, so there is no
    single "the tools" for the whole run; the entry catalog plus the `progressive`
    flag in `params` is the honest description, and it is stable across runs of the
    same configuration — which is the property the fingerprint exists to have.
    """
    try:
        dataset_id = store.capture_dataset_id()
        if not dataset_id:
            return None
        # A delegating turn can produce a dozen sub-runs; some datasets want only
        # the top-level ones.
        if parent_turn_id and not _setting("trajectories.captureDelegates", True):
            return None
        fingerprint = store.upsert_harness(
            HarnessWrite(
                agent_id=agent_id,
                model=model,
                provider=provider,
                system_prompt=_system_prompt_from(messages),
                tool_names=_tool_names(tools),
                tool_schemas={
                    str((t.get("function") or {}).get("name") or i): t
                    for i, t in enumerate(tools)
                },
                params=params,
                label=f"{agent_id or 'main'} @ {model or '?'}",
            )
        )
        # A delegated turn's id is `<parent>:<agent>:<hex>`, so the parent run is
        # findable by its turn id — the delegation tree comes free with the seam.
        parent_run_id = None
        if parent_turn_id:
            parent = store.find_by_turn_id(parent_turn_id)
            parent_run_id = parent.id if parent else None
        source, node_id = _provenance(conn)
        run_id = store.start_run(
            dataset_id,
            source=source,
            node_id=node_id,
            turn_id=turn_id,
            parent_run_id=parent_run_id,
            harness=fingerprint,
            agent_id=agent_id,
            agent_name=agent_name,
            model=model,
            provider=provider,
            goal=_goal_from(messages),
        )
        recorder = RunRecorder(run_id)
        recorder.message(0, _goal_from(messages), role="user")
        # Enforce retention here rather than on a timer: this is the only moment
        # the dataset is known to have just grown.
        keep = int(_setting("trajectories.retentionRuns", 5000) or 0)
        if keep > 0:
            store.prune(dataset_id, keep)
        return recorder
    except Exception as exc:  # pragma: no cover - capture is never load-bearing
        logger.debug("trajectories: capture could not start for %s: %s", turn_id, exc)
        return None
