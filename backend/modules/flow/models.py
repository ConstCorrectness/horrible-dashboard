from typing import Any

from pydantic import BaseModel

# Flow ids are slug or generated hex, matching the workspace id rule.
FLOW_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class FlowNode(BaseModel):
    """One node in a flow graph. `type` selects the node behavior (e.g.
    'trigger.prompt', 'agent', 'output.pane'); `config` is the node's settings
    (model, system prompt, …), stored largely opaquely — only the executor reads it."""

    id: str
    type: str
    position: dict[str, float] = {}
    config: dict[str, Any] = {}


class FlowEdge(BaseModel):
    """A directed wire from one node's output to another's input (React Flow shape)."""

    id: str | None = None
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class Flow(BaseModel):
    """A saved orchestration graph. Round-tripped opaquely by the store; the
    executor interprets node `type`/`config` at run time. See flow-canvas.md."""

    id: str
    name: str
    nodes: list[FlowNode] = []
    edges: list[FlowEdge] = []


class FlowsState(BaseModel):
    """The whole collection plus which flow is active (last opened)."""

    active: str | None = None
    flows: list[Flow] = []


class CreateFlow(BaseModel):
    name: str


class UpsertFlow(BaseModel):
    """Partial update: only fields present in the body are applied (via
    `model_fields_set`), so saving the graph never clobbers the name."""

    name: str | None = None
    nodes: list[FlowNode] | None = None
    edges: list[FlowEdge] | None = None


class ActiveRequest(BaseModel):
    id: str
