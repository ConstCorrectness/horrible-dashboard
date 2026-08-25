"""HTTP surface for agentpedia, mounted at `/api/agentpedia`.

Two routes and no store. The timeline lists turns; opening one joins it into the
four columns the stepper scrubs. Harness and tool pages are served by
`/api/trajectories/{harnesses,tools,compare,stats}`, which already return that data
— duplicating them here would mean two aggregates to keep in step, and the second
one would be the one that drifts.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from backend.modules.agentpedia import join
from backend.modules.agentpedia.models import TurnIndexResponse, TurnView

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agentpedia", tags=["agentpedia"])


def _capture_on() -> bool:
    """Is any dataset capturing trajectories? The difference between a turn that
    did nothing and nothing having been recording — which is what an empty Did
    column otherwise looks like."""
    try:
        from backend.modules.trajectories import store as traj

        return traj.capture_dataset_id() is not None
    except Exception:
        logger.debug("agentpedia: capture check failed", exc_info=True)
        return False


@router.get("/turns", response_model=TurnIndexResponse)
def timeline(
    limit: int = Query(50, ge=1, le=500),
    agent_id: str | None = None,
    since: float | None = None,
    roots_only: bool = False,
) -> TurnIndexResponse:
    """The Runs timeline, newest first.

    Reads the **durable** `agent_turns` table rather than the interpretability
    ring: a stepper whose history ends 25 turns ago cannot answer "what was the
    agent doing this morning", which is most of why anyone opens it.
    """
    from backend.modules.interpretability import store as turns

    rows = turns.list_turns(
        limit, agent_id=agent_id, since=since, roots_only=roots_only
    )
    return TurnIndexResponse(
        turns=[join.index_entry(row) for row in rows], capture_on=_capture_on()
    )


@router.get("/turns/{turn_id}", response_model=TurnView)
def turn(turn_id: str) -> TurnView:
    """One turn, joined: shown, wire, did and cost for every round.

    The ring first and then the table, for the reason `GET
    /api/interpretability/turns/{turn_id}` does the same — a turn reached from the
    timeline is by definition one the ring has already dropped.
    """
    from backend.modules.interpretability import recorder
    from backend.modules.interpretability import store as turns

    snapshot = recorder.get_turn(turn_id) or turns.get_turn(turn_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No captured turn {turn_id!r}")
    return join.turn_view(snapshot)
