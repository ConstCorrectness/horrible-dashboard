"""Fork a recorded turn: change one thing, run it again, diff the two.

This is the same intervention the lens performs on a trace, one altitude up. There
the edit is a token and the readout is a logit; here the edit is the context — a
tool removed, the system prompt rewritten, a different model — and the readout is
what the agent decided to do. `derived_from`, `edits`, `diff`: one vocabulary.

## No second loop

The fork runs `run_agent_loop`, the same function a chat turn runs. That is the
rule evals already follows and it is the only way the result means anything: what
is under test is *this app's* catalog assembly, budget cap, progressive disclosure
and permission gate. A replay that built its own message list and called the
provider directly would be measuring a harness nobody ships.

## Nothing acts, by default

Three legs can cause a side effect, and answering only the first would have been a
comfortable illusion:

1. the browser leg — covered by `OfflineConnection`, whose fixtures resolve the
   pending future the way a real pane would;
2. backend tools (`agent.delegate`, `agent.ask_peer`) — resolved server-side, they
   never touch a connection;
3. backend-plugin tools — likewise.

So the loop takes a `simulate` hook that stands in for all three at the one point
after the gate where a call would otherwise act. `live: true` removes it, and then
the fork runs on the **real browser connection** — which is what makes "live" more
than a flag: the tools genuinely run, and the orchestrator's own permission gate
prompts the user in the UI exactly as it would in a chat turn. There is no path
where a tool acts and nobody was asked.

## What is stored

Only the edge (`store.py`). The fork's rounds, wire and steps are recorded by the
ordinary machinery under the fork's own `turn_id`, so it opens in the stepper like
any other turn.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from backend.modules.agentpedia import store
from backend.modules.agentpedia.models import (
    ForkDiff,
    ForkEdit,
    ForkPreview,
    ForkRecord,
    ForkRequest,
    RebuildReport,
    SideDiff,
    ToolDrift,
)
from backend.modules.interpretability.models import RoundSnapshot, TurnSnapshot

logger = logging.getLogger(__name__)

#: A fork that has not finished in this long is a failure worth seeing rather than
#: a request that never returns. Same number and same reasoning as an eval case: a
#: small model on CPU is genuinely slow, and a false timeout reads as a finding.
FORK_TIMEOUT_S = 300.0


class ForkError(Exception):
    """Something the caller can fix — a missing turn, a round out of range, `live`
    with no browser attached. Routes turn it into a 4xx; the agent tool returns it
    as an error string."""


# ── Reading the parent ───────────────────────────────────────────────────────


def load_turn(turn_id: str) -> TurnSnapshot:
    """The parent turn: the ring first, then the durable table.

    Same order as `GET /turns/{id}`, for the same reason — a turn worth forking is
    usually one you have been reading, and a turn reached from the timeline is by
    definition one the ring has dropped.
    """
    from backend.modules.interpretability import recorder
    from backend.modules.interpretability import store as turns

    snapshot = recorder.get_turn(turn_id) or turns.get_turn(turn_id)
    if snapshot is None:
        raise ForkError(f"No captured turn {turn_id!r}")
    return snapshot


def _round(turn: TurnSnapshot, index: int) -> RoundSnapshot:
    if not turn.rounds:
        raise ForkError(
            f"Turn {turn.turnId!r} has no rounds to branch from"
            + (
                " — it reached a peer's node, which assembles its own context"
                if turn.kind == "peer"
                else ""
            )
        )
    if not 0 <= index < len(turn.rounds):
        raise ForkError(
            f"Round {index} is out of range; this turn has {len(turn.rounds)}"
        )
    return turn.rounds[index]


def _spec(agent_id: str) -> Any:
    from backend.modules.agent.roster import get_agent

    return get_agent(agent_id) or get_agent("main")


def decision_at(turn: TurnSnapshot, round_no: int) -> list[str]:
    """What the model reached for when handed round `round_no`'s context.

    Read off the *next* round's blocks rather than from the trajectory steps,
    because trajectory capture is off by default while `agent_turns` is always on —
    and because it then works identically for the parent and for the fork, which is
    what makes the two sides of the diff comparable rather than merely adjacent.

    An empty list means the model answered without calling anything. That is a
    decision too, and the diff says so rather than showing a blank.
    """
    from backend.modules.agentpedia.rebuild import _call_name, _split_tool_calls

    if round_no + 1 >= len(turn.rounds):
        return []
    before = len(turn.rounds[round_no].blocks)
    for block in turn.rounds[round_no + 1].blocks[before:]:
        if block.role != "assistant":
            continue
        _text, calls = _split_tool_calls(block.content or "")
        if calls:
            return [_call_name(call) for call in calls]
        return []
    return []


# ── Building the fork ────────────────────────────────────────────────────────


def _denied_tools(
    tools: list[dict[str, Any]], edits: list[ForkEdit], report: RebuildReport
) -> set[str]:
    """The names this fork takes out of the catalog.

    Resolved against the catalog rather than taken on trust: a `drop_tool` naming a
    tool that is not offered this round removes nothing, and a fork whose headline
    finding is "removing it changed nothing" must not be able to reach that
    conclusion because of a typo.
    """
    from backend.modules.agent.orchestrator import _group_of

    offered = {t["function"]["name"] for t in tools}
    denied: set[str] = set()
    for edit in edits:
        if edit.op == "drop_tool":
            name = (edit.name or "").strip()
            if name in offered:
                denied.add(name)
                report.applied.append(f"drop_tool({name})")
            else:
                report.rejected.append(
                    f"drop_tool: {name!r} is not in this round's catalog"
                )
        elif edit.op == "drop_group":
            group = (edit.name or "").strip()
            matched = {n for n in offered if _group_of(n) == group}
            if matched:
                denied |= matched
                report.applied.append(f"drop_group({group}, {len(matched)} tools)")
            else:
                report.rejected.append(
                    f"drop_group: no tool from group {group!r} is offered this round"
                )
    return denied


def _drift(recorded: list[str], offered: list[str], denied: set[str]) -> ToolDrift:
    return ToolDrift(
        added=sorted(set(offered) - set(recorded)),
        missing=sorted(set(recorded) - set(offered)),
        denied=sorted(denied),
    )


def _provider_for(
    turn: TurnSnapshot, edits: list[ForkEdit]
) -> tuple[Any, str, str, float | None]:
    """`(info, endpoint, model, temperature)` for the fork.

    Resolved through `roster.resolve_provider` — the same call every
    `run_agent_loop` call site must make — and *then* overridden by the edits.
    Starting from the recorded turn's provider name instead would be the wrong
    default: a model name means nothing on a server that does not have that model,
    and the recorded name is a `kind`, not an endpoint.
    """
    from backend.modules.agent import providers as P
    from backend.modules.agent.roster import resolve_provider
    from backend.modules.agent.routes import _endpoint_for, _load_config

    config = _load_config()
    if config is None:
        raise ForkError("The agent is not configured on this node")
    info, endpoint = resolve_provider(config, turn.agentId)
    model = turn.model or getattr(config, "model", "")
    temperature = turn.temperature

    for edit in edits:
        if edit.op == "set_model" and (edit.name or "").strip():
            model = (edit.name or "").strip()
        elif edit.op == "set_provider" and (edit.name or "").strip():
            kind = (edit.name or "").strip()
            if kind not in P.PROVIDERS:
                raise ForkError(
                    f"Unknown provider {kind!r}; this node has "
                    + ", ".join(sorted(P.PROVIDERS))
                )
            info = P.provider_for(kind)
            # Its own default (or its live spawned endpoint), never the saved one —
            # that endpoint belongs to the provider the user configured globally.
            endpoint = _endpoint_for(info, None)
        elif edit.op == "set_temperature" and edit.value is not None:
            temperature = float(edit.value)
    return info, endpoint, model, temperature


def _plan(req: ForkRequest) -> dict[str, Any]:
    """Everything a fork needs, assembled without running anything.

    Shared by `preview` and `run`, so what the preview shows is what the run does —
    two assemblers would drift, and the one that drifted would be the preview,
    which is the half nobody checks.
    """
    from backend.modules.agent.offline_conn import OfflineConnection, live_agent_tools
    from backend.modules.agent.orchestrator import _select_tools
    from backend.modules.agentpedia import rebuild

    turn = load_turn(req.turn_id)
    snapshot = _round(turn, req.from_round)
    spec = _spec(turn.agentId)

    messages, report = rebuild.messages_from(
        snapshot, system_prompt=getattr(spec, "system_prompt", "") or ""
    )
    messages = rebuild.apply_edits(messages, snapshot, req.edits, report)

    conn = OfflineConnection(live_agent_tools(), req.fixtures)
    active_groups = set(snapshot.activeGroups)
    tools = _select_tools(conn, active_groups, spec)
    denied = _denied_tools(tools, req.edits, report)
    drift = _drift(
        [t.name for t in snapshot.tools],
        [t["function"]["name"] for t in tools],
        denied,
    )
    info, endpoint, model, temperature = _provider_for(turn, req.edits)

    for edit in req.edits:
        if edit.op in ("set_model", "set_provider", "set_temperature"):
            report.applied.append(f"{edit.op}({edit.name or edit.value})")

    return {
        "turn": turn,
        "snapshot": snapshot,
        "spec": spec,
        "conn": conn,
        "messages": messages,
        "report": report,
        "tools": [t for t in tools if t["function"]["name"] not in denied],
        "denied": denied,
        "drift": drift,
        "active_groups": active_groups,
        "info": info,
        "endpoint": endpoint,
        "model": model,
        "temperature": temperature,
    }


def preview(req: ForkRequest) -> ForkPreview:
    """What this fork would run. No provider call, no side effects, no record."""
    plan = _plan(req)
    return ForkPreview(
        turn_id=req.turn_id,
        from_round=req.from_round,
        messages=plan["messages"],
        rebuild=plan["report"],
        drift=plan["drift"],
        tools=[t["function"]["name"] for t in plan["tools"]],
        model=plan["model"],
        provider=str(getattr(plan["info"], "kind", "")),
        temperature=plan["temperature"],
    )


# ── Running it ───────────────────────────────────────────────────────────────


class _RecordingConn:
    """A live browser connection with a tap on it.

    Used only by a `live` fork, where the connection has to be the real socket (the
    tools genuinely run and the gate genuinely prompts) but the fork still needs to
    report which tools were called. Attribute access is delegated, so `pending` and
    `pending_approvals` are the *same dict objects* the socket's receive loop
    resolves futures in — a copy here would mean every relayed tool call hanging
    until the timeout.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def send_json(self, data: dict[str, Any]) -> None:
        if data.get("channel") == "agent" and data.get("event") == "tool_call":
            self.calls.append(str((data.get("data") or {}).get("name") or ""))
        await self._inner.send_json(data)


def _live_connection() -> Any:
    """The browser socket a live fork runs on, or an error saying why not.

    The richest manifest, matching `live_agent_tools` — a second window still
    registering its panes must not be able to hand the fork a shorter catalog than
    the one the user is looking at.
    """
    from backend.modules.ws import _active_connections

    best = None
    best_tools = -1
    for conn in list(_active_connections):
        count = len(getattr(conn, "agent_tools", None) or [])
        if count > best_tools:
            best, best_tools = conn, count
    if best is None:
        raise ForkError(
            "A live fork runs its tools for real, so it needs the browser that owns"
            " them: open the dashboard in a window and run it again."
        )
    return best


async def run(req: ForkRequest) -> ForkRecord:
    """Run the fork and record the edge. Raises `ForkError` for a bad request; a
    provider failure is recorded on the row instead, because "this edit made the
    model fail" is itself the answer to the question that was asked."""
    from backend.modules.agent.orchestrator import run_agent_loop
    from backend.modules.agent.permissions import Mode
    from backend.modules.agent.roster import resolve_mode

    plan = _plan(req)
    turn: TurnSnapshot = plan["turn"]
    fork_turn_id = f"{req.turn_id}:fork:{uuid.uuid4().hex[:8]}"

    offline = plan["conn"]
    simulated: list[str] = []

    async def simulate(name: str, args: dict[str, Any]) -> Any:
        """Stand in for every tool that acts.

        Calls are recorded here rather than read off the connection afterwards,
        because backend tools and plugin tools never reach a connection at all —
        `OfflineConnection.calls` would quietly have listed only the browser half.
        """
        _ = args
        simulated.append(name)
        return offline.fixture_for(name)

    if req.live:
        conn: Any = _RecordingConn(_live_connection())
        simulate_hook = None
        mode = resolve_mode(plan["spec"])
    else:
        conn = offline
        simulate_hook = simulate
        # Forced, for the reason an eval forces it: a counterfactual whose result
        # depends on whoever's saved permission rules happened to be loaded is not
        # a counterfactual. Nothing acts anyway — `simulate` is what makes that
        # true, not the mode.
        mode = Mode.AUTONOMOUS

    async def emit(reasoning: str, content: str) -> None:
        _ = (reasoning, content)

    record = ForkRecord(
        fork_turn_id=fork_turn_id,
        parent_turn_id=req.turn_id,
        from_round=req.from_round,
        created_at=time.time(),
        edits=req.edits,
        live=req.live,
        model=plan["model"],
        provider=str(getattr(plan["info"], "kind", "")),
        rebuild=plan["report"],
        drift=plan["drift"],
    )

    try:
        answer = await asyncio.wait_for(
            run_agent_loop(
                conn,
                fork_turn_id,
                plan["messages"],
                plan["tools"],
                plan["info"],
                plan["endpoint"],
                plan["model"],
                emit,
                temperature=plan["temperature"]
                if plan["temperature"] is not None
                else 0.0,
                context_size=turn.requestedNumCtx,
                active_groups=plan["active_groups"],
                spec=plan["spec"],
                mode_override=mode,
                simulate=simulate_hook,
                deny_tools=plan["denied"],
            ),
            timeout=FORK_TIMEOUT_S,
        )
        record.answer = answer
    except TimeoutError:
        record.status = "failed"
        record.error = f"fork timed out after {FORK_TIMEOUT_S:.0f}s"
    except Exception as exc:
        logger.warning("agentpedia: fork of %s failed: %s", req.turn_id, exc)
        record.status = "failed"
        record.error = f"{type(exc).__name__}: {exc}"

    # A live fork's calls come off the tap on the socket; a simulated one's come
    # from the hook, which is the only place that sees all three legs.
    record.calls = list(conn.calls) if req.live else list(simulated)
    store.save_fork(record)
    return record


# ── The diff ─────────────────────────────────────────────────────────────────


def _side(turn: TurnSnapshot, round_no: int) -> SideDiff:
    snapshot = turn.rounds[round_no] if 0 <= round_no < len(turn.rounds) else None
    return SideDiff(
        turn_id=turn.turnId,
        model=turn.model,
        provider=turn.provider,
        rounds=len(turn.rounds),
        total_tokens=turn.rounds[-1].totalTokens if turn.rounds else 0,
        tools_offered=len(snapshot.tools) if snapshot else 0,
        calls=_all_calls(turn, round_no),
        decision=decision_at(turn, round_no),
    )


def _all_calls(turn: TurnSnapshot, from_round: int) -> list[str]:
    """Every tool the turn called from `from_round` on — the whole branch, not just
    the first move. The decision is the headline; this is what it led to."""
    calls: list[str] = []
    for index in range(from_round, len(turn.rounds)):
        calls.extend(decision_at(turn, index))
    return calls


def _final_answer(turn_id: str) -> str:
    """The parent's final answer, if anything recorded it.

    `agent_turns` cannot supply this: the last assistant message is appended
    *after* the last round was captured, so it is in no snapshot. The trajectory
    store has it (`RunRecorder.finish` writes it as a message step) whenever
    capture was on for the turn. Empty otherwise, and the diff shows an empty
    parent answer as "not recorded" rather than as an empty reply — the same rule
    the wire column follows about what an empty column claims.
    """
    try:
        from backend.modules.trajectories import store as traj

        run = traj.find_by_turn_id(turn_id)
        if run is None:
            return ""
        detail = traj.get_run(run.id, with_steps=True)
        messages = [
            step
            for step in (detail.step_list if detail else [])
            if step.kind == "message" and step.role == "assistant" and step.content
        ]
        return messages[-1].content if messages else ""
    except Exception:
        logger.debug("agentpedia: parent answer lookup failed", exc_info=True)
        return ""


def diff(fork_turn_id: str) -> ForkDiff:
    """Parent beside fork, at the round the fork branched from.

    The parent is read at `from_round` and the fork at round 0, because that is the
    same instant: the fork's first round *is* the parent's branch round with the
    edits applied. Comparing round 0 to round 0 would line the fork's opening move
    up against a decision the parent made several rounds earlier.
    """
    record = store.get_fork(fork_turn_id)
    if record is None:
        raise ForkError(f"No fork {fork_turn_id!r}")
    parent = load_turn(record.parent_turn_id)
    child = load_turn(record.fork_turn_id)

    a = _side(parent, record.from_round)
    b = _side(child, 0)
    a.answer = _final_answer(parent.turnId)
    b.answer = record.answer

    parent_tools = (
        {t.name for t in parent.rounds[record.from_round].tools}
        if (0 <= record.from_round < len(parent.rounds))
        else set()
    )
    fork_tools = {t.name for t in child.rounds[0].tools} if child.rounds else set()

    return ForkDiff(
        fork=record,
        a=a,
        b=b,
        tools_removed=sorted(parent_tools - fork_tools),
        tools_added=sorted(fork_tools - parent_tools),
        same_decision=a.decision == b.decision,
        token_delta=b.total_tokens - a.total_tokens,
    )
