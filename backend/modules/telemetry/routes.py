from typing import Any

from fastapi import APIRouter, Query

from backend.modules.telemetry import drain, store
from backend.modules.telemetry.models import IoEvent
from backend.modules.telemetry.recorder import recorder

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/recent", response_model=list[IoEvent])
def recent() -> list[IoEvent]:
    """Backlog of recent I/O events, so a freshly opened widget isn't empty."""
    return recorder.recent()


@router.get("/turn/{turn_id}")
def for_turn(
    turn_id: str,
    round_no: int | None = Query(None, alias="round"),
) -> dict[str, Any]:
    """Durable wire traffic for one agent turn.

    The live ring holds 500 events and forgets everything else, so this is the only
    way to read the I/O of a turn that finished any distance in the past -- which is
    every turn a postmortem is actually about.
    """
    return {"events": store.for_turn(turn_id, round_no=round_no)}


@router.get("/stats")
def stats() -> dict[str, int]:
    """How much has been persisted, and how much was lost getting there.

    `dropped` is inferred from gaps in the recorder's event ids. It is reported
    rather than prevented: adding backpressure to `record()` would slow the app down
    to protect a debugging feature. A visible number beats silent loss.
    """
    return drain.stats()
