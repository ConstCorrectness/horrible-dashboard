"""Saved flows: a collection of orchestration graphs with an active selection.

Mirrors the workspace store — each flow's `nodes`/`edges` are round-tripped and the
backend interprets only its own `id`/`name`/`active` fields here; the executor reads
node types/config at run time. File-backed JSON under $HORRIBLE_DATA_DIR.
See docs/modules/flow-canvas.md.
"""

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi import Path as PathParam

from backend import jsonstore, paths
from backend.modules.flow.models import (
    FLOW_ID_PATTERN,
    ActiveRequest,
    CreateFlow,
    Flow,
    FlowsState,
    UpsertFlow,
)

router = APIRouter(prefix="/flows", tags=["flows"])

FlowId = Annotated[str, PathParam(pattern=FLOW_ID_PATTERN)]


def _state_path() -> Path:
    return paths.data_dir() / "flows.json"


def _read() -> FlowsState:
    text = jsonstore.read_text(_state_path())
    if text is None:
        return FlowsState()
    try:
        return FlowsState.model_validate_json(text)
    except ValueError:
        return FlowsState()


def _write(state: FlowsState) -> None:
    jsonstore.write_text(_state_path(), state.model_dump_json())


def _find(state: FlowsState, flow_id: str) -> Flow | None:
    return next((f for f in state.flows if f.id == flow_id), None)


def load_flow(flow_id: str) -> Flow | None:
    """Fetch one flow by id (used by the executor)."""
    return _find(_read(), flow_id)


@router.get("", response_model=FlowsState)
def list_flows() -> FlowsState:
    return _read()


@router.post("", response_model=Flow)
@jsonstore.serialized(_state_path)
def create_flow(body: CreateFlow) -> Flow:
    state = _read()
    flow = Flow(id=uuid.uuid4().hex[:8], name=body.name, nodes=[], edges=[])
    state.flows.append(flow)
    state.active = flow.id
    _write(state)
    return flow


@router.put("/active", response_model=FlowsState)
@jsonstore.serialized(_state_path)
def set_active(body: ActiveRequest) -> FlowsState:
    state = _read()
    if _find(state, body.id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown flow '{body.id}'")
    state.active = body.id
    _write(state)
    return state


@router.get("/{flow_id}", response_model=Flow)
def get_flow(flow_id: FlowId) -> Flow:
    flow = _find(_read(), flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"Unknown flow '{flow_id}'")
    return flow


@router.put("/{flow_id}", response_model=Flow)
@jsonstore.serialized(_state_path)
def upsert_flow(flow_id: FlowId, body: UpsertFlow) -> Flow:
    state = _read()
    existing = _find(state, flow_id)
    if existing is not None:
        # Apply only provided fields so a rename doesn't wipe the graph.
        if "name" in body.model_fields_set:
            existing.name = body.name or existing.name
        if "nodes" in body.model_fields_set and body.nodes is not None:
            existing.nodes = body.nodes
        if "edges" in body.model_fields_set and body.edges is not None:
            existing.edges = body.edges
        flow = existing
    else:
        flow = Flow(
            id=flow_id,
            name=body.name or flow_id,
            nodes=body.nodes or [],
            edges=body.edges or [],
        )
        state.flows.append(flow)
    if state.active is None:
        state.active = flow.id
    _write(state)
    return flow


@router.delete("/{flow_id}", response_model=FlowsState)
@jsonstore.serialized(_state_path)
def delete_flow(flow_id: FlowId) -> FlowsState:
    state = _read()
    state.flows = [f for f in state.flows if f.id != flow_id]
    if state.active == flow_id:
        state.active = state.flows[0].id if state.flows else None
    _write(state)
    return state
