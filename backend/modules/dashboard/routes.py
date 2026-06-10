import os
from pathlib import Path

from fastapi import APIRouter

from backend.modules.dashboard.models import DEFAULT_LAYOUT, DashboardLayout

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _layout_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "dashboard-layout.json"


@router.get("/layout", response_model=DashboardLayout)
def get_layout() -> DashboardLayout:
    path = _layout_path()
    if path.is_file():
        return DashboardLayout.model_validate_json(path.read_text())
    return DEFAULT_LAYOUT


@router.put("/layout", response_model=DashboardLayout)
def put_layout(layout: DashboardLayout) -> DashboardLayout:
    path = _layout_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(layout.model_dump_json())
    return layout
