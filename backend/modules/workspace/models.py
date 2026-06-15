from typing import Any

from pydantic import BaseModel

# Workspace ids are kebab/slug or generated hex. The dashboard's id is the stable
# slug "dashboard" so commands can select it; user-created ones get a random id.
WORKSPACE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class Workspace(BaseModel):
    """One named dockview layout. `layout` is the engine's serialized blob,
    stored opaquely — the backend never interprets it (see windowing.md)."""

    id: str
    name: str
    layout: dict[str, Any] | None = None


class WorkspacesState(BaseModel):
    """The whole collection plus which workspace is active."""

    active: str | None = None
    workspaces: list[Workspace] = []


class CreateWorkspace(BaseModel):
    name: str


class UpsertWorkspace(BaseModel):
    """Partial update: only fields present in the request body are applied
    (distinguished via `model_fields_set`), so a rename never clobbers layout."""

    name: str | None = None
    layout: dict[str, Any] | None = None


class ActiveRequest(BaseModel):
    id: str
