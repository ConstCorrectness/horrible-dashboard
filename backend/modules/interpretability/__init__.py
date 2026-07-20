"""Interpretability: see exactly what the model is being handed.

The agent loop builds a fresh context every round and sends it — the system prompt,
tool guides, replayed history, the focused editor buffer, the user turn, and a tool
list that progressive disclosure recomputes as the model calls `load_tools`. None of
that was ever visible. This module captures each round and renders it: what's in the
window, what each piece costs in real tokens, and when the tool budget silently drops
tools on the floor.

Read-only by construction — see recorder.py. Observing the context must not change it.
"""

from backend.modules.interpretability.routes import router

__all__ = ["router"]
