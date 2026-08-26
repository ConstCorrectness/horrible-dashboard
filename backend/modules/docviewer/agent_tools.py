"""The `docviewer` agent tool group.

Three tools, grouped so they cost nothing on a turn that never mentions
documentation — an ungrouped backend tool spends schema bytes on every turn.

The split is by what someone asks for. `docviewer.search` is the one that carries
the weight ("what does this say about X"), because a doc set exists to be looked
things up in; `docviewer.listSets` is how the model learns which sets exist before
searching one; and `docviewer.openPage` puts a page in front of the human, which is
the only reason to name a page rather than quote it.

Capturing a set is deliberately **not** a tool. It drives a real browser at a
user-supplied URL for several minutes and fills a library — that is a decision with a
form behind it, not something to be inferred from a sentence.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.docviewer import store
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

_GROUP = "docviewer"


async def _list_sets(_args: dict[str, Any]) -> dict[str, Any]:
    return {
        "sets": [
            {
                "id": s["id"],
                "title": s["title"],
                "seedUrl": s["seed_url"],
                "pages": s["page_count"],
                "status": s["status"],
            }
            for s in store.list_sets()
        ]
    }


async def _search(args: dict[str, Any]) -> dict[str, Any]:
    from backend.modules.docviewer.models import SearchRequest
    from backend.modules.docviewer.routes import search_set

    set_id = str(args.get("setId") or "")
    query = str(args.get("query") or "")
    if not set_id or not query:
        return {"error": "setId and query are both required"}
    limit = int(args.get("limit") or 8)
    result = await search_set(set_id, SearchRequest(query=query, limit=limit))
    return {
        "hits": [
            {
                "pageId": hit.page_id,
                "title": hit.title,
                "url": hit.url,
                "snippet": hit.snippet,
            }
            for hit in result.hits
        ]
    }


async def _open_page(args: dict[str, Any]) -> dict[str, Any]:
    """Ask the frontend to show a page. The layout tool relay does the opening —
    this only resolves the id and reports what it points at."""
    page = store.get_page(str(args.get("pageId") or ""))
    if page is None:
        return {"error": "no such page"}
    return {
        "open": {
            "panel": "docviewer.browse",
            "params": {"setId": page["set_id"], "pageId": page["id"]},
        },
        "title": page["title"],
        "url": page["url"],
    }


_TOOLS = [
    AgentTool(
        name="docviewer.listSets",
        description=(
            "List the documentation sets captured on this machine — whole docs sites "
            "saved for offline reading. Use this to find the set id before searching."
        ),
        handler=_list_sets,
        group=_GROUP,
    ),
    AgentTool(
        name="docviewer.search",
        description=(
            "Search one captured documentation set and return the matching passages "
            "with the page they came from. This reads the user's own saved copy of "
            "the docs, so it works offline and reflects the version they captured."
        ),
        handler=_search,
        parameters={
            "setId": {
                "type": "string",
                "description": "The set id from docviewer.listSets.",
            },
            "query": {
                "type": "string",
                "description": "What to look for, in natural language.",
            },
            "limit": {
                "type": "number",
                "description": "How many passages to return. Default 8.",
            },
        },
        group=_GROUP,
    ),
    AgentTool(
        name="docviewer.openPage",
        description=(
            "Show one captured documentation page to the user in the doc viewer. "
            "Use after docviewer.search when they should read the page themselves."
        ),
        handler=_open_page,
        parameters={
            "pageId": {
                "type": "string",
                "description": "The page id from a docviewer.search hit.",
            }
        },
        group=_GROUP,
    ),
]


def register_agent_tools() -> None:
    """Register the group. Called from `backend/app.py` at startup."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
