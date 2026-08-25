"""HTTP surface for agentpedia, mounted at `/api/agentpedia`.

Two halves. The **reads** join what four other modules already recorded: the
timeline lists turns, and opening one joins it into the four columns the stepper
scrubs. Harness and tool pages are served by
`/api/trajectories/{harnesses,tools,compare,stats}`, which already return that data
— duplicating them here would mean two aggregates to keep in step, and the second
one would be the one that drifts.

The **fork** routes are the only ones that write, and the only ones that cost
anything: `POST /fork` runs a real model turn. They own exactly one table, the
counterfactual edge (see store.py); the fork's own rounds, wire and steps are
recorded by the ordinary machinery under its own turn id.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from backend.modules.agentpedia import fork, join, store
from backend.modules.agentpedia.models import (
    ForkDiff,
    ForkListResponse,
    ForkPreview,
    ForkRecord,
    ForkRequest,
    TurnIndexResponse,
    TurnView,
)

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


# ── Forks ────────────────────────────────────────────────────────────────────
#
# A fork writes: it runs a real model turn. Everything above this line is a read.
# The split is worth keeping visible — the two halves fail in completely different
# ways, and only one of them can cost you money or a side effect.


@router.post("/fork", response_model=ForkRecord)
async def create_fork(req: ForkRequest) -> ForkRecord:
    """Re-run a recorded turn with something changed.

    Tools are **simulated** unless `live` is set, and a simulated fork cannot cause
    a side effect through any of the three legs a tool call can take (browser,
    backend, backend plugin) — see fork.py. `live` runs on the browser's own
    connection, so the tools really run and the permission gate really prompts.

    A provider failure is a 200 with `status: "failed"`: "that edit made the model
    fall over" is an answer to the question that was asked, not a broken request.
    """
    try:
        return await fork.run(req)
    except fork.ForkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/fork/preview", response_model=ForkPreview)
def preview_fork(req: ForkRequest) -> ForkPreview:
    """What the fork would run — rebuilt messages, catalog, drift — without running
    it. A fork costs a model turn, and half the questions ("does this context even
    rebuild cleanly", "which tools does that drop") are answered before it starts.
    """
    try:
        return fork.preview(req)
    except fork.ForkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/forks", response_model=ForkListResponse)
def list_forks(
    limit: int = Query(100, ge=1, le=500), parent: str | None = None
) -> ForkListResponse:
    """Fork edges, newest first, optionally only those branched off one turn."""
    return ForkListResponse(forks=store.list_forks(limit, parent_turn_id=parent))


@router.get("/forks/{fork_turn_id}/diff", response_model=ForkDiff)
def fork_diff(fork_turn_id: str) -> ForkDiff:
    """Parent beside fork at the branch round: tools offered, the decision, the
    answer, the cost."""
    try:
        return fork.diff(fork_turn_id)
    except fork.ForkError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/forks/{fork_turn_id}")
def delete_fork(fork_turn_id: str) -> dict[str, bool]:
    """Forget the counterfactual link. The fork's own turn stays in `agent_turns`,
    where it is an ordinary turn and the stepper can still open it."""
    return {"deleted": store.delete_fork(fork_turn_id)}
