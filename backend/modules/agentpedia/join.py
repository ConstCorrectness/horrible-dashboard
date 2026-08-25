"""Joining one turn out of four records that were never designed together.

Everything here is a read. Agentpedia's whole claim is that the node already
records enough to reconstruct a turn — shown, wire, did, cost — and that nobody had
ever put the four beside each other. If this file ever needs to *write* something,
that is a sign the recording end is missing a field, and the fix belongs there.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.agentpedia.models import (
    DidStep,
    FlattenReport,
    RoundCost,
    RoundView,
    RunLink,
    TurnIndexEntry,
    TurnView,
    WireEvent,
    WireStatus,
)
from backend.modules.interpretability.models import RoundSnapshot, TurnSnapshot

logger = logging.getLogger(__name__)

#: Sources worth showing beside a round. `inbound` is deliberately excluded: the
#: request that *started* the turn is not something the turn did, and including it
#: would put the browser's own poll traffic in the middle of the model's reasoning.
_WIRE_SOURCES = {"outbound", "browser"}


def flatten_report(round_snapshot: RoundSnapshot) -> FlattenReport:
    """What the provider seam does to this round's messages on the way out.

    Runs the **real** `normalize_system_messages` over the round's blocks rather
    than reimplementing its rule, so this cannot drift from what actually ships.
    The block contents are previews, so the *text* is approximate — the *shape*
    change, which is the entire point of the column, is exact.
    """
    from backend.modules.agent.providers import normalize_system_messages

    messages = [
        {"role": b.role, "content": b.content or " "} for b in round_snapshot.blocks
    ]
    try:
        out = normalize_system_messages(messages)
    except Exception:  # a view must never be the thing that breaks
        logger.debug("agentpedia: flatten report failed", exc_info=True)
        return FlattenReport(messages_in=len(messages), messages_out=len(messages))

    merged: list[str] = []
    seen_non_system = False
    for block in round_snapshot.blocks:
        if block.role != "system":
            seen_non_system = True
        elif not seen_non_system:
            merged.append(block.label)
    return FlattenReport(
        messages_in=len(messages), messages_out=len(out), merged=merged
    )


def wire_for(turn_id: str, round_no: int) -> list[WireEvent]:
    """The requests this round made, from the telemetry ring.

    Keyed on the `turn_id`/`round` stamp rather than a time window: two agents can
    run concurrently on one node, and a timestamp range would interleave them into
    a transcript that reads as one agent behaving erratically.
    """
    from backend.modules.telemetry.recorder import recorder

    fields = set(WireEvent.model_fields)
    return [
        WireEvent(**event.model_dump(include=fields))
        for event in recorder.recent()
        if event.turn_id == turn_id
        and event.round == round_no
        and event.source in _WIRE_SOURCES
    ]


def wire_status(turn: TurnSnapshot, found: int) -> WireStatus:
    """Why the wire column is empty, when it is.

    An empty list has three causes and they are not the same fact: the ring has
    since overflowed, the turn predates the `turn_id` stamp (or ran with telemetry
    off), or the round genuinely made no request. Only the first two are worth a
    banner, and telling them apart is what stops a reader concluding the agent
    never called the provider at all.
    """
    from backend.modules.telemetry.recorder import recorder

    if found:
        return "live"
    events = recorder.recent()
    if events and turn.startedAt and events[0].ts > turn.startedAt:
        # The oldest thing the ring still holds is newer than this turn, so whatever
        # this turn sent has been pushed out of the buffer since.
        return "aged_out"
    return "unrecorded"


def _run_link(run: Any) -> RunLink:
    return RunLink(
        id=run.id,
        dataset_id=run.dataset_id,
        status=str(run.status),
        outcome=str(run.outcome) if run.outcome else None,
        goal=run.goal,
        steps=run.steps,
        harness=run.harness,
        duration_ms=run.duration_ms,
    )


def _did_steps(detail: Any, round_no: int) -> list[DidStep]:
    return [
        DidStep(
            seq=s.seq,
            kind=str(s.kind),
            name=s.name,
            ok=s.ok,
            gated=s.gated,
            duration_ms=s.duration_ms,
            tokens=s.tokens,
            error=s.error,
            content=s.content,
            args=s.args,
            result=s.result,
        )
        for s in (detail.step_list if detail else [])
        if s.round == round_no
    ]


def _cost(round_snapshot: RoundSnapshot, window: int | None) -> RoundCost:
    total = round_snapshot.totalTokens
    return RoundCost(
        message_tokens=round_snapshot.messageTokens,
        tool_tokens=round_snapshot.toolTokens,
        total_tokens=total,
        window=window,
        window_pct=(total / window * 100) if window and total else None,
    )


def turn_view(turn: TurnSnapshot) -> TurnView:
    """One turn with all four columns filled in, round by round."""
    from backend.modules.trajectories import store as traj

    run = None
    detail = None
    try:
        run = traj.find_by_turn_id(turn.turnId)
        if run is not None:
            detail = traj.get_run(run.id, with_steps=True)
    except Exception:
        # Trajectory capture is off by default and its database may not exist. The
        # shown and wire halves are still worth rendering, so this degrades rather
        # than 500s — the stance the recorder takes about its own store.
        logger.debug("agentpedia: trajectory join failed", exc_info=True)

    window = turn.modelContextLength or turn.requestedNumCtx
    rounds: list[RoundView] = []
    found = 0
    for snapshot in turn.rounds:
        wire = wire_for(turn.turnId, snapshot.round)
        found += len(wire)
        rounds.append(
            RoundView(
                round=snapshot.round,
                shown=snapshot,
                wire=wire,
                did=_did_steps(detail, snapshot.round),
                cost=_cost(snapshot, window),
                flatten=flatten_report(snapshot),
            )
        )

    return TurnView(
        turn_id=turn.turnId,
        parent_turn_id=turn.parentTurnId,
        agent_id=turn.agentId,
        agent_name=turn.agentName,
        kind=turn.kind,
        peer_id=turn.peerId,
        model=turn.model,
        provider=turn.provider,
        started_at=turn.startedAt,
        exact=turn.exact,
        tokenizer_repo=turn.tokenizerRepo,
        tokenizer_source=turn.tokenizerSource,
        requested_num_ctx=turn.requestedNumCtx,
        model_context_length=turn.modelContextLength,
        temperature=turn.temperature,
        rounds=rounds,
        run=_run_link(run) if run is not None else None,
        wire_status=wire_status(turn, found),
    )


def index_entry(summary: dict[str, Any]) -> TurnIndexEntry:
    """One timeline row: a stored turn summary plus its run, if capture was on."""
    from backend.modules.trajectories import store as traj

    run = None
    try:
        run = traj.find_by_turn_id(summary["turnId"])
    except Exception:
        logger.debug("agentpedia: run lookup failed", exc_info=True)
    return TurnIndexEntry(
        turn_id=summary["turnId"],
        parent_turn_id=summary.get("parentTurnId"),
        agent_id=summary.get("agentId") or "main",
        agent_name=summary.get("agentName") or "",
        kind=summary.get("kind") or "local",
        model=summary.get("model") or "",
        provider=summary.get("provider") or "",
        started_at=summary.get("startedAt") or 0.0,
        rounds=summary.get("rounds") or 0,
        total_tokens=summary.get("totalTokens") or 0,
        run=_run_link(run) if run is not None else None,
    )
