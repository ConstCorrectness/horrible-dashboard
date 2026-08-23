"""The model designer's back end: a design graph, its shapes, and its PyTorch source.

The rest of this module *inspects* models that already exist — a GGUF's tensor
inventory, a prompt as the provider will receive it. This package is the other
direction: the IR the node editor edits, and the `nn.Module` subclass it becomes.

Layered deliberately, so the parts that must not need torch don't:

    models.py     the IR (Pydantic, the wire format)
    spec.py       the node catalog — sockets, params, shapes, emitted code
    primitives.py the nn.Module classes a generated file carries with it
    walk.py       ordering and wiring, shared so shapes and codegen cannot drift
    shapes.py     tier-1 validation: symbolic, instant, pure Python
    codegen.py    graph -> source
    examples.py   templates, which are also the codegen fixtures
"""

from __future__ import annotations

from backend.modules.interpretability.graph.codegen import generate
from backend.modules.interpretability.graph.models import (
    CodeResult,
    DesignGraph,
    GraphEdge,
    GraphNode,
    ShapeIssue,
    ShapeReport,
    SubGraph,
)
from backend.modules.interpretability.graph.shapes import infer
from backend.modules.interpretability.graph.spec import catalog

__all__ = [
    "CodeResult",
    "DesignGraph",
    "GraphEdge",
    "GraphNode",
    "ShapeIssue",
    "ShapeReport",
    "SubGraph",
    "catalog",
    "generate",
    "infer",
]
