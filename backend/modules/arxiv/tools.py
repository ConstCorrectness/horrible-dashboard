"""Backend agent tools for arXiv (grouped under `arxiv.` — the name prefix is the
group). Search/get are read-only; download files a paper into the library and is
permission-gated as a side effect."""

from __future__ import annotations

import dataclasses
from typing import Any

from backend.modules.arxiv import client
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

_SUMMARY_CAP = 1200
_SEARCH_CAP = 10


def _entry_dict(entry: client.ArxivEntry, *, full: bool = False) -> dict[str, Any]:
    d = dataclasses.asdict(entry)
    if not full:
        d["summary"] = d["summary"][:_SUMMARY_CAP]
    return d


async def _search(args: dict[str, Any]) -> dict[str, Any]:
    total, entries = await client.search(
        str(args.get("query", "")),
        start=int(args.get("start", 0) or 0),
        max_results=min(int(args.get("max_results", 5) or 5), _SEARCH_CAP),
        category=str(args.get("category") or "") or None,
        sort=str(args.get("sort") or "relevance"),
    )
    return {"total": total, "entries": [_entry_dict(e) for e in entries]}


async def _get(args: dict[str, Any]) -> dict[str, Any]:
    entry = await client.get_paper(str(args.get("arxiv_id", "")))
    return _entry_dict(entry, full=True)


async def _download(args: dict[str, Any]) -> dict[str, Any]:
    from backend.modules.research import service

    entry = await client.get_paper(str(args.get("arxiv_id", "")))
    result = await service.save_pdf_url(
        entry.pdf_url,
        library=str(args.get("library") or "default"),
        title=entry.title,
        tags=sorted({*(args.get("tags") or []), "arxiv"}),
        source_url=entry.abs_url,
        author=", ".join(entry.authors[:6]) or None,
    )
    source = result["source"]
    return {
        "artifact_id": result["artifact"]["id"],
        "source_id": source["id"],
        "title": source["title"],
        "status": source["status"],
    }


_TOOLS = [
    AgentTool(
        name="arxiv.search",
        description=(
            "Search arXiv (title/abstract/authors). Returns papers with ids, "
            "abstracts, categories, and PDF links. Start broad, then narrow."
        ),
        handler=_search,
        parameters={
            "query": {"type": "string", "description": "Search terms"},
            "category": {
                "type": "string",
                "description": "Optional arXiv category filter, e.g. cs.LG, stat.ML",
            },
            "max_results": {"type": "integer", "description": "1-10 (default 5)"},
            "start": {"type": "integer", "description": "Pagination offset"},
            "sort": {
                "type": "string",
                "enum": ["relevance", "lastUpdatedDate", "submittedDate"],
            },
        },
        required=["query"],
    ),
    AgentTool(
        name="arxiv.get",
        description="Full metadata (complete abstract, authors, links) for one arXiv id.",
        handler=_get,
        parameters={
            "arxiv_id": {
                "type": "string",
                "description": "e.g. 2107.03374 or cs/0112017",
            }
        },
        required=["arxiv_id"],
    ),
    AgentTool(
        name="arxiv.download",
        description=(
            "Download a paper's PDF into the artifact store and file it into a "
            "knowledge library (text extracted and ingested for search). Use after "
            "arxiv.search when a paper is worth keeping."
        ),
        handler=_download,
        parameters={
            "arxiv_id": {"type": "string"},
            "library": {
                "type": "string",
                "description": "Target library (default: default)",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        required=["arxiv_id"],
        side_effect=True,
        specifier_template="{arxiv_id}",
    ),
]


def register_arxiv_tools() -> None:
    """Insert the arXiv backend tools into the sdk registry (called from app.py)."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
