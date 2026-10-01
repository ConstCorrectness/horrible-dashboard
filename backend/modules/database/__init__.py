"""Database module: a general-purpose, plug-and-play database inspector.

The pane is a psql-like SQL console that queries any connected database through a
pluggable driver layer (sqlite / postgres+pgvector / duckdb / mysql). The app's own
local vector store is exposed as the built-in ``app`` connection.

The vector engine (``vectorstore``) and ``embeddings`` are preserved and re-exported
here so backend consumers — notably agent-commons matchmaking — keep importing them
from this module.
"""

from __future__ import annotations

from typing import Any

# Resolved on first access (PEP 562), not at import. Importing *any* submodule runs
# this file, and `routes` drags in the agent stack and the settings store — so
# `hassault/results.py` asking for `app_db.ensure_app_db_dir` (a path helper) put
# all of that in the standalone game server's import graph.
_EXPORTS = {
    "router": "backend.modules.database.routes",
    "delete_document": "backend.modules.database.vectorstore",
    "get_db_stats": "backend.modules.database.vectorstore",
    "init_db": "backend.modules.database.vectorstore",
    "list_documents": "backend.modules.database.vectorstore",
    "search_documents": "backend.modules.database.vectorstore",
    "upsert_document": "backend.modules.database.vectorstore",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value  # cache: later lookups skip this hook
    return value
