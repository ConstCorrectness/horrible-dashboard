"""The `symbols` agent tool group: semantic + exact lookup over the symdex index.

`symbols.searchDocs` is the retrieval surface the coder/dba agents preload:
package APIs, database tables, and project docs by meaning. `symbols.lookup` is
the exact-name path into the same data via the relational `code_symbols`
projection (fast, works offline). Registered into the sdk registry from app.py
like the training/games tools — first-party consumers of the plugin surface.
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.modules.lsp import symbol_store
from backend.modules.symdex.index import symdex_index
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool


async def _search_docs(args: dict[str, Any]) -> Any:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "symbols.searchDocs needs a query"}
    kind = str(args.get("kind", "")).strip() or None
    if kind not in (None, "packages", "schema", "docs"):
        return {"error": f"unknown kind {kind!r} (packages|schema|docs)"}
    limit = args.get("limit", 8)
    try:
        limit = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        limit = 8
    return await symdex_index.search(query, kind, limit)


async def _lookup(args: dict[str, Any]) -> Any:
    name = str(args.get("name", "")).strip()
    if not name:
        return {"error": "symbols.lookup needs a name"}
    lang = str(args.get("lang", "python")) or "python"
    items = await asyncio.to_thread(symbol_store.query, lang, name, 10, None)
    return {"name": name, "items": items}


_TOOLS = [
    AgentTool(
        name="symbols.searchDocs",
        description=(
            "Semantically search the symbol/docs index: installed package APIs "
            "(classes, functions, signatures, docstrings), database schemas "
            "(kind='schema' — find tables by what they hold), and this app's "
            "documentation (kind='docs'). Use before writing code against an "
            "unfamiliar API or SQL against unfamiliar tables."
        ),
        parameters={
            "query": {
                "type": "string",
                "description": "What you're looking for, e.g. 'http client post request'.",
            },
            "kind": {
                "type": "string",
                "description": "Optional filter: packages|schema|docs.",
            },
            "limit": {"type": "number", "description": "Max results (default 8)."},
        },
        required=["query"],
        handler=_search_docs,
        group="symbols",
    ),
    AgentTool(
        name="symbols.lookup",
        description=(
            "Exact prefix lookup of a symbol by name in the code-symbol index "
            "(buffer symbols + indexed package APIs), with signature and doc "
            "snippet. Fast and offline; use when you know the name."
        ),
        parameters={
            "name": {"type": "string", "description": "Symbol name or prefix."},
            "lang": {"type": "string", "description": "Language id (default python)."},
        },
        required=["name"],
        handler=_lookup,
        group="symbols",
    ),
]


def register_agent_tools() -> None:
    """Insert the symdex backend tools into the sdk registry (called from app.py)."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
