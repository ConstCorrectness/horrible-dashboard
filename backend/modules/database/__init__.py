"""Database module: a general-purpose, plug-and-play database inspector.

The pane is a psql-like SQL console that queries any connected database through a
pluggable driver layer (sqlite / postgres+pgvector / duckdb / mysql). The app's own
local vector store is exposed as the built-in ``app`` connection.

The vector engine (``vectorstore``) and ``embeddings`` are preserved and re-exported
here so backend consumers — notably agent-commons matchmaking — keep importing them
from this module.
"""

from backend.modules.database.routes import router
from backend.modules.database.vectorstore import (
    delete_document,
    get_db_stats,
    init_db,
    list_documents,
    search_documents,
    upsert_document,
)

__all__ = [
    "router",
    "delete_document",
    "get_db_stats",
    "init_db",
    "list_documents",
    "search_documents",
    "upsert_document",
]
