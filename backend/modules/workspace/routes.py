"""Named workspaces: a collection of dockview layouts with an active selection.

Each workspace's `layout` is the docking engine's serialized blob, round-tripped
opaquely — the backend interprets only its own `id`/`name`/`active` fields. The
frontend authors the default "Dashboard" layout (engine-shaped) and saves it
here. See docs/architecture/windowing.md.
"""

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi import Path as PathParam

from backend import jsonstore, paths
from backend.modules.workspace.models import (
    WORKSPACE_ID_PATTERN,
    ActiveRequest,
    CreateWorkspace,
    UpsertWorkspace,
    Workspace,
    WorkspacesState,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

WorkspaceId = Annotated[str, PathParam(pattern=WORKSPACE_ID_PATTERN)]


def _state_path() -> Path:
    return paths.data_dir() / "workspaces.json"


def _read() -> WorkspacesState:
    text = jsonstore.read_text(_state_path())
    if text is None:
        return WorkspacesState()
    try:
        return WorkspacesState.model_validate_json(text)
    except ValueError:
        return WorkspacesState()


def _write(state: WorkspacesState) -> None:
    jsonstore.write_text(_state_path(), state.model_dump_json())


def _find(state: WorkspacesState, ws_id: str) -> Workspace | None:
    return next((w for w in state.workspaces if w.id == ws_id), None)


@router.get("", response_model=WorkspacesState)
def list_workspaces() -> WorkspacesState:
    return _read()


@router.post("", response_model=Workspace)
@jsonstore.serialized(_state_path)
def create_workspace(body: CreateWorkspace) -> Workspace:
    state = _read()
    ws = Workspace(id=uuid.uuid4().hex[:8], name=body.name, layout=None)
    state.workspaces.append(ws)
    if state.active is None:
        state.active = ws.id
    _write(state)
    return ws


@router.put("/active", response_model=WorkspacesState)
@jsonstore.serialized(_state_path)
def set_active(body: ActiveRequest) -> WorkspacesState:
    state = _read()
    if _find(state, body.id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown workspace '{body.id}'")
    state.active = body.id
    _write(state)
    return state


@router.put("/{ws_id}", response_model=Workspace)
@jsonstore.serialized(_state_path)
def upsert_workspace(ws_id: WorkspaceId, body: UpsertWorkspace) -> Workspace:
    state = _read()
    existing = _find(state, ws_id)
    if existing is not None:
        # Apply only provided fields so a rename doesn't wipe the layout.
        if "name" in body.model_fields_set:
            existing.name = body.name or existing.name
        if "layout" in body.model_fields_set:
            existing.layout = body.layout
        ws = existing
    else:
        ws = Workspace(id=ws_id, name=body.name or ws_id, layout=body.layout)
        state.workspaces.append(ws)
    if state.active is None:
        state.active = ws.id
    _write(state)
    return ws


@router.delete("/{ws_id}", response_model=WorkspacesState)
@jsonstore.serialized(_state_path)
def delete_workspace(ws_id: WorkspaceId) -> WorkspacesState:
    state = _read()
    state.workspaces = [w for w in state.workspaces if w.id != ws_id]
    if state.active == ws_id:
        state.active = state.workspaces[0].id if state.workspaces else None
    _write(state)
    return state
