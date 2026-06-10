import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/workspace", tags=["workspace"])


class WorkspaceLayout(BaseModel):
    """The docking engine's serialized layout, stored opaquely.

    The backend never interprets the shape — it round-trips whatever the
    frontend's windowing engine produces (see docs/architecture/windowing.md).
    `None` means no saved layout yet, so the frontend builds its default.
    """

    layout: dict[str, Any] | None = None


def _layout_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "workspace-layout.json"


@router.get("/layout", response_model=WorkspaceLayout)
def get_layout() -> WorkspaceLayout:
    path = _layout_path()
    if path.is_file():
        return WorkspaceLayout.model_validate_json(path.read_text())
    return WorkspaceLayout(layout=None)


@router.put("/layout", response_model=WorkspaceLayout)
def put_layout(body: WorkspaceLayout) -> WorkspaceLayout:
    path = _layout_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.model_dump_json())
    return body
