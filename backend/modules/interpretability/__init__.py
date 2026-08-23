"""Interpretability: see exactly what the model is being handed.

The agent loop builds a fresh context every round and sends it — the system prompt,
tool guides, replayed history, the focused editor buffer, the user turn, and a tool
list that progressive disclosure recomputes as the model calls `load_tools`. None of
that was ever visible. This module captures each round and renders it: what's in the
window, what each piece costs in real tokens, and when the tool budget silently drops
tools on the floor.

Read-only by construction — see recorder.py. Observing the context must not change it.

The `graph` subpackage is the exception, and deliberately so: it is the same pane's
other mode, where you *design* a model rather than inspect one. It shares the pane
because of the bridge between them — the model you are looking at is the obvious
thing to fork.
"""

from fastapi import APIRouter

from backend.modules.interpretability.graph.routes import router as graph_router
from backend.modules.interpretability.routes import router as _inspect_router

router = APIRouter()
router.include_router(_inspect_router)
# Mounted after, so `/interpretability/turns` and friends keep matching first; the
# designer's paths all sit under `/interpretability/graph`.
router.include_router(graph_router)

__all__ = ["router"]
