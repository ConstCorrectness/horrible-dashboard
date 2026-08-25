"""Agent tools for agentpedia — the agent reading, and re-running, its own turns.

This is the exploratory payoff of the whole module: an agent that can step through
the turn it just took, see which tools it was actually offered and what each one
cost it, and then ask "what would I have done without that tool" — and get an
answer by running it rather than by speculating.

Names are `agentpedia.<verb>` because the orchestrator derives a tool's group from
the **name prefix** (`_group_of`), not from `AgentTool.group` — the two must agree
or the group is silently wrong. Grouped, so the schemas cost nothing until an agent
loads them.

`agentpedia.fork` is `side_effect=True`. Not because a simulated fork acts — it
cannot, that is the point of the `simulate` hook — but because it spends a real
model turn, and `live: true` is a genuine actuator sitting behind the same tool.
The gate is what makes an agent asking to re-run its own history a visible step.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.agentpedia import fork, join, store
from backend.modules.agentpedia.models import ForkEdit, ForkRequest
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

#: How much of a block's text a tool result carries. The pane can afford a 4 KB
#: preview; a context window cannot afford several of them, and an agent asking
#: "what was I shown" almost always wants the shape and the cost, not the prose.
BLOCK_CHARS = 400


def _round_brief(view: Any) -> dict[str, Any]:
    return {
        "round": view.round,
        "blocks": [
            {
                "kind": block.kind,
                "label": block.label,
                "tokens": block.tokens,
                "preview": (block.content or "")[:BLOCK_CHARS],
            }
            for block in view.shown.blocks
        ],
        "tools": [t.name for t in view.shown.tools],
        "toolsTruncated": view.shown.toolsTruncated,
        "activeGroups": view.shown.activeGroups,
        "did": [
            {"name": step.name, "kind": step.kind, "ok": step.ok, "gated": step.gated}
            for step in view.did
        ],
        "cost": {
            "messageTokens": view.cost.message_tokens,
            "toolTokens": view.cost.tool_tokens,
            "totalTokens": view.cost.total_tokens,
            "windowPct": view.cost.window_pct,
        },
        "wireRequests": len(view.wire),
    }


async def _step(args: dict[str, Any]) -> dict[str, Any]:
    turn_id = str(args.get("turn_id") or "").strip()
    if not turn_id:
        # The overwhelmingly common case: "what did I just do". Answered without
        # making the agent go and find an id first.
        from backend.modules.interpretability import store as turns

        recent = turns.list_turns(1, roots_only=True)
        if not recent:
            return {"error": "no turns have been recorded on this node"}
        turn_id = str(recent[0]["turnId"])

    try:
        turn = fork.load_turn(turn_id)
    except fork.ForkError as exc:
        return {"error": str(exc)}
    view = join.turn_view(turn)

    wanted = args.get("round")
    rounds = view.rounds
    if wanted is not None:
        index = int(wanted)
        rounds = [r for r in rounds if r.round == index]
        if not rounds:
            return {"error": f"turn {turn_id} has no round {index}"}
    return {
        "turnId": view.turn_id,
        "agent": view.agent_id,
        "model": view.model,
        "provider": view.provider,
        "rounds": len(view.rounds),
        "wireStatus": view.wire_status,
        "exactTokens": view.exact,
        "detail": [_round_brief(r) for r in rounds],
    }


async def _harness(args: dict[str, Any]) -> dict[str, Any]:
    """Harness pages, straight from the trajectories store.

    Not re-aggregated here: `/api/trajectories` already computes this, and a second
    aggregate would be the one that drifts. Same call the pane makes.
    """
    from backend.modules.trajectories import analyze, store as traj

    fingerprint = str(args.get("fingerprint") or "").strip()
    if not fingerprint:
        return {
            "harnesses": [
                {
                    "fingerprint": h.fingerprint,
                    "label": h.label,
                    "agent": h.agent_id,
                    "model": h.model,
                    "tools": len(h.tool_names),
                    "runs": h.run_count,
                }
                for h in traj.list_harnesses(20)
            ]
        }
    harness = traj.get_harness(fingerprint)
    if harness is None:
        return {"error": f"no harness {fingerprint!r}"}
    return {
        "fingerprint": harness.fingerprint,
        "label": harness.label,
        "agent": harness.agent_id,
        "model": harness.model,
        "provider": harness.provider,
        "systemPrompt": harness.system_prompt[:2000],
        "tools": harness.tool_names,
        "runs": harness.run_count,
        "toolStats": analyze.tool_stats(harness=fingerprint, limit=30),
    }


def _edits_from(raw: Any) -> list[ForkEdit]:
    """Coerce the model's `edits` argument into edits.

    A list of objects is the schema; a JSON *string* containing that list is what
    several small models emit instead, and discarding it silently is exactly the
    bug the evals module found in `load_tools`. So a string is parsed, and anything
    still unreadable raises rather than becoming an empty edit list — a fork with
    no edits is a re-run, and it would be reported as one.
    """
    import json

    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list):
        raise ValueError("edits must be a list of {op, ...} objects")
    return [ForkEdit.model_validate(item) for item in raw]


async def _fork(args: dict[str, Any]) -> dict[str, Any]:
    try:
        edits = _edits_from(args.get("edits") or [])
    except Exception as exc:
        return {"error": f"could not read edits: {exc}"}
    if not edits:
        return {
            "error": "a fork with no edits is just a re-run; name at least one edit"
            " (drop_tool, drop_group, set_system, edit_message, set_model,"
            " set_provider, set_temperature, truncate_history)"
        }
    request = ForkRequest(
        turn_id=str(args.get("turn_id") or "").strip(),
        from_round=int(args.get("from_round") or 0),
        edits=edits,
        # Never live from a tool call. A live fork prompts a human through the
        # permission gate for every acting call, which is a conversation the agent
        # cannot have on the user's behalf; the pane's button is where that lives.
        live=False,
    )
    try:
        record = await fork.run(request)
    except fork.ForkError as exc:
        return {"error": str(exc)}
    return {
        "forkTurnId": record.fork_turn_id,
        "parentTurnId": record.parent_turn_id,
        "status": record.status,
        "error": record.error,
        "calls": record.calls,
        "answer": record.answer[:2000],
        "exactRebuild": record.rebuild.exact,
        "rejectedEdits": record.rebuild.rejected,
        "toolsDenied": record.drift.denied,
    }


async def _diff(args: dict[str, Any]) -> dict[str, Any]:
    fork_turn_id = str(args.get("fork_turn_id") or "").strip()
    if not fork_turn_id:
        forks = store.list_forks(1)
        if not forks:
            return {"error": "no forks have been taken on this node"}
        fork_turn_id = forks[0].fork_turn_id
    try:
        result = fork.diff(fork_turn_id)
    except fork.ForkError as exc:
        return {"error": str(exc)}
    return {
        "forkTurnId": result.fork.fork_turn_id,
        "edits": [e.model_dump(exclude_none=True) for e in result.fork.edits],
        "sameDecision": result.same_decision,
        "toolsRemoved": result.tools_removed,
        "toolsAdded": result.tools_added,
        "tokenDelta": result.token_delta,
        "before": {
            "decision": result.a.decision,
            "calls": result.a.calls,
            "toolsOffered": result.a.tools_offered,
            "answer": result.a.answer[:1200],
        },
        "after": {
            "decision": result.b.decision,
            "calls": result.b.calls,
            "toolsOffered": result.b.tools_offered,
            "answer": result.b.answer[:1200],
        },
    }


_TOOLS = [
    AgentTool(
        name="agentpedia.step",
        description=(
            "Read a recorded agent turn round by round: the context blocks and what"
            " each cost, the tools offered, what was called, and how much of the"
            " window it used. Defaults to the most recent turn."
        ),
        handler=_step,
        parameters={
            "turn_id": {
                "type": "string",
                "description": "Turn to read; omit for the most recent one",
            },
            "round": {
                "type": "integer",
                "description": "One round only; omit for every round",
            },
        },
        required=[],
        group="agentpedia",
    ),
    AgentTool(
        name="agentpedia.harness",
        description=(
            "The harness pages: which configurations (agent, prompt, tools, sampling)"
            " have run here, how often, and how each tool fared under them. Omit the"
            " fingerprint to list them."
        ),
        handler=_harness,
        parameters={
            "fingerprint": {
                "type": "string",
                "description": "Harness fingerprint; omit to list",
            }
        },
        required=[],
        group="agentpedia",
    ),
    AgentTool(
        name="agentpedia.fork",
        description=(
            "Re-run a recorded turn with something changed and see what the agent"
            " does instead. Edits are objects: {op: 'drop_tool', name: 'files.write'},"
            " {op: 'drop_group', name: 'editor'}, {op: 'set_system', content: '...'},"
            " {op: 'edit_message', index: 3, content: '...'}, {op: 'set_model',"
            " name: '...'}, {op: 'set_provider', name: '...'}, {op: 'set_temperature',"
            " value: 0.7}, {op: 'truncate_history', keep: 2}. Tools are simulated —"
            " nothing actually happens — so this is safe to run on a turn that wrote"
            " files or sent messages."
        ),
        handler=_fork,
        parameters={
            "turn_id": {"type": "string", "description": "The turn to fork"},
            "from_round": {
                "type": "integer",
                "description": "Which round to branch at (default 0, the whole turn)",
            },
            "edits": {
                "type": "array",
                "description": "The changes to make; at least one",
                "items": {"type": "object"},
            },
        },
        required=["turn_id", "edits"],
        side_effect=True,
        group="agentpedia",
    ),
    AgentTool(
        name="agentpedia.diff",
        description=(
            "Compare a fork against the turn it came from: tools offered, the first"
            " move each made at the branch round, the calls that followed, the final"
            " answers and the token cost. Defaults to the most recent fork."
        ),
        handler=_diff,
        parameters={
            "fork_turn_id": {
                "type": "string",
                "description": "The fork; omit for the most recent",
            }
        },
        required=[],
        group="agentpedia",
    ),
]


def register_agent_tools() -> None:
    """Insert the agentpedia tools into the sdk registry (called from app.py)."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
