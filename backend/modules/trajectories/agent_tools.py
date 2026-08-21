"""Agent tools for trajectories — the read path of the continual-learning loop.

The point of `trajectories.search` is that the agent can look up how it handled a
similar task before and use that as context. Which makes one default load-bearing:

**Retrieved examples are successes unless asked otherwise.** Handing a model its
own failed runs as few-shot context teaches the failure. It is the same insight
`evals/export.py` states about exporting the *ideal* trajectory rather than the
model's, applied to retrieval instead of training. `include_failures` exists for
when you are explicitly asking "how do I usually get this wrong", and it says so.

Names are `trajectories.<verb>` because the orchestrator derives a tool's group
from the name prefix (`_group_of`), not from `AgentTool.group` — the two must
agree or the group is silently wrong.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.trajectories import analyze, search, store
from backend.modules.trajectories.models import LabelWrite
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger("trajectories")

#: Steps returned by `trajectories.get` before it starts summarising. A whole run
#: pasted into a context window is how a helpful tool becomes a context-window
#: overflow.
MAX_STEPS_INLINE = 40


def _step_brief(step: Any) -> dict[str, Any]:
    """One step, small enough to put in a prompt.

    Arguments are included (they are the interesting part of an action) but the
    *result* is reduced to a shape, because a 200 KB file read would otherwise
    fill the window with something the agent already knows how to fetch.
    """
    brief: dict[str, Any] = {"seq": step.seq, "kind": step.kind}
    if step.name:
        brief["name"] = step.name
    if step.args is not None:
        brief["args"] = step.args
    if step.content:
        brief["content"] = step.content[:600]
    if step.ok is not None:
        brief["ok"] = step.ok
    if step.error:
        brief["error"] = step.error[:300]
    if step.result is not None:
        summary = repr(step.result)
        brief["result"] = summary[:400] + ("…" if len(summary) > 400 else "")
    return brief


def _run_brief(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "goal": run.goal,
        "outcome": run.outcome,
        "status": run.status,
        "agent": run.agent_id,
        "model": run.model,
        "steps": run.steps,
        "harness": run.harness,
        "durationMs": run.duration_ms,
    }


async def _search(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    include_failures = bool(args.get("include_failures"))
    limit = max(1, min(int(args.get("limit") or 5), 20))
    runs, method = await search.search_runs(
        query,
        limit=limit,
        dataset_id=str(args.get("dataset") or "") or None,
        outcome=None if include_failures else "success",
    )
    note = (
        "Includes failed runs — treat them as counter-examples, not templates."
        if include_failures
        else "Successful runs only. Pass include_failures to see how this goes wrong."
    )
    if method == "substring":
        # Say so. A model that thinks it ran a semantic search and got nothing
        # concludes there is no similar run; the truth is nobody could embed the
        # query, and a differently-worded past run may well exist.
        note += " No embedder was reachable, so this was a substring match, not a"
        note += " semantic one — a similar run worded differently would be missed."
    return {"runs": [_run_brief(r) for r in runs], "method": method, "note": note}


async def _get(args: dict[str, Any]) -> dict[str, Any]:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return {"error": "run_id is required"}
    run = store.get_run(run_id)
    if run is None:
        return {"error": f"no run {run_id}"}
    steps = run.step_list
    truncated = len(steps) > MAX_STEPS_INLINE
    if truncated:
        # Keep the head and the tail: how it started and how it ended are what
        # a postmortem needs; the middle of a 200-step loop is repetition.
        half = MAX_STEPS_INLINE // 2
        steps = steps[:half] + steps[-half:]
    return {
        "run": _run_brief(run),
        "steps": [_step_brief(s) for s in steps],
        "truncated": truncated,
        "labels": [
            {"key": lbl.key, "value": lbl.value, "source": lbl.source}
            for lbl in run.labels
        ],
    }


async def _stats(args: dict[str, Any]) -> dict[str, Any]:
    return analyze.dataset_stats(str(args.get("dataset") or "") or None)


async def _compare(args: dict[str, Any]) -> dict[str, Any]:
    a = str(args.get("a") or "").strip()
    b = str(args.get("b") or "").strip()
    if not a or not b:
        return {"error": "both a and b (harness fingerprints) are required"}
    for fingerprint in (a, b):
        if store.get_harness(fingerprint) is None:
            return {"error": f"no harness {fingerprint}"}
    return analyze.compare(a, b)


async def _label(args: dict[str, Any]) -> dict[str, Any]:
    run_id = str(args.get("run_id") or "").strip()
    key = str(args.get("key") or "").strip()
    if not run_id or not key:
        return {"error": "run_id and key are required"}
    if store.get_run(run_id, with_steps=False) is None:
        return {"error": f"no run {run_id}"}
    label = store.add_label(
        run_id,
        LabelWrite(
            key=key,
            value=str(args.get("value") or ""),
            score=args.get("score"),
            # Always `agent-critic` from here, never whatever the model claims:
            # a model that could label its own verdict "human" would launder a
            # guess into evidence.
            source="agent-critic",
            rationale=str(args.get("rationale") or ""),
            step_seq=args.get("step_seq"),
        ),
    )
    return {
        "status": "labelled",
        "id": label.id,
        "key": label.key,
        "value": label.value,
    }


_TOOLS: list[AgentTool] = [
    AgentTool(
        name="trajectories.search",
        description=(
            "Find past agent runs by what they were trying to do (semantic search)."
            " Returns successful runs by default — use them as worked examples of"
            " how a similar task was handled before."
        ),
        handler=_search,
        parameters={
            "query": {
                "type": "string",
                "description": "Text to match against run goals",
            },
            "dataset": {"type": "string", "description": "Restrict to one dataset"},
            "include_failures": {
                "type": "boolean",
                "description": "Also return failed runs, as counter-examples",
            },
            "limit": {"type": "integer", "description": "Max runs (1-20, default 5)"},
        },
        required=["query"],
        group="trajectories",
    ),
    AgentTool(
        name="trajectories.get",
        description=(
            "Read one past run step by step: its tool calls, their arguments and"
            " whether they succeeded. Long runs are trimmed to the head and tail."
        ),
        handler=_get,
        parameters={"run_id": {"type": "string", "description": "The run id"}},
        required=["run_id"],
        group="trajectories",
    ),
    AgentTool(
        name="trajectories.stats",
        description=(
            "Aggregate counts across stored runs: outcomes, average steps, and which"
            " tools are called most and fail most."
        ),
        handler=_stats,
        parameters={
            "dataset": {"type": "string", "description": "Restrict to one dataset"}
        },
        required=[],
        group="trajectories",
    ),
    AgentTool(
        name="trajectories.compare",
        description=(
            "Compare two harness fingerprints — success rates, per-tool call"
            " frequency, and the goals one handles that the other does not."
        ),
        handler=_compare,
        parameters={
            "a": {"type": "string", "description": "Baseline harness fingerprint"},
            "b": {"type": "string", "description": "Candidate harness fingerprint"},
        },
        required=["a", "b"],
        group="trajectories",
    ),
    AgentTool(
        name="trajectories.label",
        description=(
            "Attach a judgment to a past run, e.g. key='outcome' value='failure',"
            " or key='failure_mode'. Recorded as an agent critique, not a human one."
        ),
        handler=_label,
        parameters={
            "run_id": {"type": "string", "description": "The run id"},
            "key": {"type": "string", "description": "Label key, e.g. outcome"},
            "value": {"type": "string", "description": "Label value"},
            "score": {"type": "number", "description": "Optional numeric score"},
            "rationale": {"type": "string", "description": "Why"},
            "step_seq": {
                "type": "integer",
                "description": "Label one step instead of the whole run",
            },
        },
        required=["run_id", "key"],
        side_effect=True,
        group="trajectories",
    ),
]


def register_agent_tools() -> None:
    """Insert the trajectory tools into the sdk registry (called from app.py)."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
