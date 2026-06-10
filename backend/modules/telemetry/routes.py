from fastapi import APIRouter

from backend.modules.telemetry.models import IoEvent
from backend.modules.telemetry.recorder import recorder

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/recent", response_model=list[IoEvent])
def recent() -> list[IoEvent]:
    """Backlog of recent I/O events, so a freshly opened widget isn't empty."""
    return recorder.recent()
