"""The deep-research subagents' internal toolset.

Distinct from `agent_tools.py` (the *user's* agent tools, permission-gated,
registered in the sdk registry): these run inside a research run with no user in
the loop, so the set is fixed, read-mostly, and every network path rides the
SSRF guard. `save_source` is the one write — it files evidence into the run's
library, which is the point of a research node.

`web_search` used to be a regex scrape of DuckDuckGo's HTML, and it was the only way
anything in this app found a URL. It now delegates to `modules.search`, which fans
out across whatever providers are configured (Tavily/Brave/Exa/Serper, a self-hosted
SearXNG, the node's own crawl index), fuses the rankings and dedupes by canonical
URL. The DDG scrape survives inside that module as the keyless fallback, so a node
with no keys behaves exactly as it did before.

The tool keeps its name because `prompts.py` names it, and gains a `depth` argument
rather than a sibling tool — one more tool in a subagent's fixed set is context spent
on every round of every subagent.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from backend.modules.arxiv import client as arxiv_client

# Re-exported: `backend/tests/test_research_engine.py` imports it from here, and the
# parser is genuinely shared — it now lives with the provider that owns it.
from backend.modules.search.providers.ddg import parse_ddg_results  # noqa: F401
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_PAGE_TEXT_CAP = 8_000
_SNIPPET_CAP = 400


async def _web_search(args: dict[str, Any]) -> dict[str, Any]:
    """Search the open web through the shared pipeline.

    Failure is a *value*, not an exception: a model routes around a broken tool far
    better than it recovers from a crashed run, and a research run that dies because
    one search timed out has thrown away everything it had gathered.
    """
    from backend.modules.search import pipeline

    if not bool(get_value("research.webSearch", True)):
        return {"error": "web search is disabled (research.webSearch setting)"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "web_search needs a query"}

    deep = str(args.get("depth") or "fast").lower() == "deep"
    limit = max(1, min(int(args.get("max_results") or 8), 10))
    try:
        answer = await (pipeline.deep_search if deep else pipeline.quick_search)(
            query, limit=limit
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the run
        logger.warning("web_search failed: %s", exc)
        return {
            "error": f"web search unavailable ({exc}); use index_search, "
            "arxiv_search or fetch_page"
        }

    results = [
        {
            "title": hit.title,
            "url": hit.url,
            "snippet": hit.snippet[:_SNIPPET_CAP],
            "found_by": hit.providers,
            **({"text": hit.text} if deep and hit.text else {}),
        }
        for hit in answer.hits
    ]
    out: dict[str, Any] = {"results": results}
    if answer.notes:
        # Carries "tavily: no API key" through to the model, so it can tell a
        # configuration gap from a genuinely empty topic.
        out["notes"] = answer.notes
    if not results:
        out["note"] = (
            "no results parsed — try different wording, or index_search / "
            "arxiv_search / fetch_page on a known site"
        )
    return out


async def _index_search(args: dict[str, Any]) -> dict[str, Any]:
    """Search the node's own crawled corpus. Free, instant, and narrow."""
    from backend.modules.search.base import SearchProviderError
    from backend.modules.search.providers.crawl import search_index

    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "index_search needs a query"}
    try:
        results = await search_index(
            query, limit=max(1, min(int(args.get("max_results") or 6), 10))
        )
    except SearchProviderError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"index search failed: {exc}"}

    if not results:
        return {
            "results": [],
            "note": (
                "nothing in the local index. It only covers seeded sites, so this "
                "is NOT evidence the web has nothing — follow up with web_search."
            ),
        }
    return {
        "results": [
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet[:_SNIPPET_CAP],
            }
            for r in results
        ]
    }


async def _fetch_page(args: dict[str, Any]) -> dict[str, Any]:
    """Read one URL as extracted article text.

    Goes through the search module's page cache, which is what stops five subagents
    in the same run each paying a full fetch and parse for the same obvious URL.
    """
    from backend.modules.search import pipeline

    url = str(args.get("url", "")).strip()
    if not url:
        return {"error": "fetch_page needs a url"}
    try:
        return await pipeline.fetch_page(url)
    except Exception as exc:  # noqa: BLE001 — bad URLs are routine mid-run
        return {"error": f"couldn't read {url}: {exc}"}


async def _arxiv_search(args: dict[str, Any]) -> dict[str, Any]:
    try:
        total, entries = await arxiv_client.search(
            str(args.get("query", "")),
            max_results=min(int(args.get("max_results", 5) or 5), 8),
            category=str(args.get("category") or "") or None,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"arxiv search failed: {exc}"}
    return {
        "total": total,
        "entries": [
            {
                "id": e.id,
                "title": e.title,
                "summary": e.summary[:800],
                "authors": e.authors[:6],
                "published": e.published,
                "abs_url": e.abs_url,
            }
            for e in entries
        ],
    }


async def _arxiv_get(args: dict[str, Any]) -> dict[str, Any]:
    try:
        entry = await arxiv_client.get_paper(str(args.get("arxiv_id", "")))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"arxiv get failed: {exc}"}
    return {
        "id": entry.id,
        "title": entry.title,
        "summary": entry.summary,
        "authors": entry.authors,
        "published": entry.published,
        "categories": entry.categories,
        "abs_url": entry.abs_url,
    }


def make_tools(library: str) -> tuple[list[dict[str, Any]], dict[str, ToolHandler]]:
    """The provider tool definitions + handler map for one run (bound to its
    library so saved evidence lands in the right place)."""
    from backend.modules.database.embeddings import get_embedding
    from backend.modules.database.vectorstore import init_db, search_documents
    from backend.modules.research import service

    async def _library_search(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            return {"error": "library_search needs a query"}
        try:
            init_db()
            embedding, _src = await get_embedding(query)
            rows = search_documents(library, embedding, 6)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"library search failed: {exc}"}
        return {
            "results": [
                {
                    "text": r["text"][:600],
                    "title": r["metadata"].get("title"),
                    "url": r["metadata"].get("url"),
                    "source_id": r["metadata"].get("source_id"),
                }
                for r in rows
            ]
        }

    async def _save_source(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url", "")).strip()
        if not url:
            return {"error": "save_source needs a url"}
        try:
            result = await service.capture_url(
                url, library=library, tags=["research-evidence"]
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"couldn't save {url}: {exc}"}
        return {
            "artifact_id": result["artifact"]["id"],
            "source_id": result["source"]["id"],
            "title": result["source"]["title"],
        }

    handlers: dict[str, ToolHandler] = {
        "web_search": _web_search,
        "index_search": _index_search,
        "fetch_page": _fetch_page,
        "arxiv_search": _arxiv_search,
        "arxiv_get": _arxiv_get,
        "library_search": _library_search,
        "save_source": _save_source,
    }

    def tool(name: str, description: str, params: dict[str, Any], required: list[str]):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": required,
                },
            },
        }

    definitions = [
        tool(
            "web_search",
            "Search the live web across every configured engine at once. Returns "
            "titles, URLs and snippets, with `found_by` naming which engines "
            "surfaced each result (agreement is a relevance signal). depth='deep' "
            "also rewrites the query, reads the top pages and returns their text — "
            "several seconds, so use it only when a fast search came back thin.",
            {
                "query": {"type": "string"},
                "depth": {"type": "string", "enum": ["fast", "deep"]},
                "max_results": {"type": "integer"},
            },
            ["query"],
        ),
        tool(
            "index_search",
            "Search this node's own crawled index of ML sites, blogs and API docs. "
            "Instant, free and rate-limit-free, but it only covers seeded sites — "
            "an empty result means 'not in the index', never 'not on the web'. Try "
            "it before web_search for documentation questions.",
            {"query": {"type": "string"}, "max_results": {"type": "integer"}},
            ["query"],
        ),
        tool(
            "fetch_page",
            "Read a URL as extracted article text (capped, cached).",
            {"url": {"type": "string"}},
            ["url"],
        ),
        tool(
            "arxiv_search",
            "Search arXiv papers (titles/abstracts/authors).",
            {
                "query": {"type": "string"},
                "category": {"type": "string", "description": "e.g. cs.LG"},
                "max_results": {"type": "integer"},
            },
            ["query"],
        ),
        tool(
            "arxiv_get",
            "Full metadata + abstract for one arXiv id.",
            {"arxiv_id": {"type": "string"}},
            ["arxiv_id"],
        ),
        tool(
            "library_search",
            "Semantic search over the user's own knowledge library.",
            {"query": {"type": "string"}},
            ["query"],
        ),
        tool(
            "save_source",
            "Archive a page worth keeping as evidence into the library.",
            {"url": {"type": "string"}},
            ["url"],
        ),
    ]
    return definitions, handlers
