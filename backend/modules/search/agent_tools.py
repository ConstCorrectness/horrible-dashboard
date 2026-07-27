"""The `search` agent tool group.

Five tools, and the count is deliberate. Tools are disclosed progressively by group
and the orchestrator holds a total budget (`TOOL_BUDGET`), so every tool here costs
context on any turn that loads the group. Crawl *seed management* is therefore an
HTTP/UI concern and not represented — the agent can start a crawl, not curate one.

The split between `search.web` and `search.deep` is a latency contract, not a quality
knob: `web` returns in well under a second and belongs inside a conversation, `deep`
rewrites, fetches and reranks and takes several seconds. The descriptions say which
to reach for, because a model given one tool with a `depth` flag reliably picks the
expensive branch.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.search import pipeline
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

_MAX_RESULTS = 10
# What each hit carries back. Enough to judge relevance and cite, not so much that
# ten results blow the context window.
_SNIPPET_CHARS = 400


def _shape(answer: pipeline.SearchAnswer, *, with_text: bool) -> dict[str, Any]:
    """The tool-result shape. Notes are always included — they are how the model
    tells "no key configured" apart from "the web has nothing on this"."""
    results = []
    for hit in answer.hits:
        row: dict[str, Any] = {
            "url": hit.url,
            "title": hit.title,
            "snippet": hit.snippet[:_SNIPPET_CHARS],
            "host": hit.host,
            # Which providers surfaced it: agreement across independent engines is a
            # relevance signal the model can use, and it makes fusion legible.
            "found_by": hit.providers,
        }
        if hit.published:
            row["published"] = hit.published
        if with_text and hit.text:
            row["text"] = hit.text
        results.append(row)

    out: dict[str, Any] = {"query": answer.query, "results": results}
    if answer.notes:
        out["notes"] = answer.notes
    if answer.rewrites:
        out["also_searched"] = answer.rewrites
    if answer.providers_used:
        out["providers"] = answer.providers_used
    if not results:
        out["note"] = (
            "no results — try different words, drop the site filter, or read a "
            "known URL directly with search.read"
        )
    return out


def _limit(args: dict[str, Any], default: int = 8) -> int:
    try:
        return max(1, min(int(args.get("limit") or default), _MAX_RESULTS))
    except (TypeError, ValueError):
        return default


def _opt(args: dict[str, Any], name: str) -> str | None:
    value = str(args.get(name) or "").strip()
    return value or None


async def _web(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "search.web needs a query"}
    answer = await pipeline.quick_search(
        query,
        limit=_limit(args),
        site=_opt(args, "site"),
        freshness=_opt(args, "freshness"),
    )
    return _shape(answer, with_text=False)


async def _deep(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "search.deep needs a query"}
    answer = await pipeline.deep_search(
        query,
        limit=_limit(args, 6),
        site=_opt(args, "site"),
        freshness=_opt(args, "freshness"),
    )
    return _shape(answer, with_text=True)


async def _read(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url:
        return {"error": "search.read needs a url"}
    try:
        return await pipeline.fetch_page(url)
    except Exception as exc:  # noqa: BLE001 — a bad URL is routine, not a crash
        return {"error": f"couldn't read {url}: {exc}"}


async def _index(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "search.index needs a query"}
    from backend.modules.search.base import SearchProviderError
    from backend.modules.search.providers.crawl import search_index

    try:
        results = await search_index(query, limit=_limit(args), site=_opt(args, "site"))
    except SearchProviderError as exc:
        return {"error": str(exc)}
    if not results:
        return {
            "results": [],
            "note": (
                "nothing in the local index — it only covers seeded sites, so this "
                "is not evidence the web has nothing. Try search.web."
            ),
        }
    return {
        "results": [
            {
                "url": r.url,
                "title": r.title,
                "snippet": r.snippet[:_SNIPPET_CHARS],
                "seed": (r.raw or {}).get("seed_id"),
            }
            for r in results
        ]
    }


async def _crawl(args: dict[str, Any]) -> dict[str, Any]:
    from backend.modules.search.crawl import store as crawl_store
    from backend.modules.tasks.queue import enqueue_task

    seed_id = str(args.get("seed_id") or "").strip()
    if not seed_id:
        return {
            "error": "search.crawl needs a seed_id — list them with the Search settings"
        }
    seed = crawl_store.get_seed(seed_id)
    if seed is None:
        known = [s["id"] for s in crawl_store.list_seeds()][:20]
        return {"error": f"unknown seed {seed_id!r}", "known_seeds": known}
    enqueue_task(
        task_type="crawl_seed",
        payload={"seed_id": seed_id, "force": bool(args.get("force"))},
    )
    return {
        "queued": True,
        "seed_id": seed_id,
        "note": (
            "crawling runs in the background and takes minutes; it also holds the "
            "shared task queue while it runs. Check back rather than waiting."
        ),
    }


_SITE_PARAM = {
    "type": "string",
    "description": "Restrict to one domain, e.g. 'arxiv.org'",
}
_FRESHNESS_PARAM = {
    "type": "string",
    "enum": ["day", "week", "month", "year"],
    "description": "Only results from the last day/week/month/year",
}
_LIMIT_PARAM = {"type": "integer", "description": f"Max results (1-{_MAX_RESULTS})"}


_TOOLS = [
    AgentTool(
        name="search.web",
        description=(
            "Search the live web and get ranked titles, URLs and snippets. Fast "
            "(under a second) — use this whenever you need current information or "
            "a URL you don't already know. Follow up with search.read to get the "
            "full text of a promising result."
        ),
        handler=_web,
        parameters={
            "query": {"type": "string", "description": "What to search for"},
            "limit": _LIMIT_PARAM,
            "site": _SITE_PARAM,
            "freshness": _FRESHNESS_PARAM,
        },
        required=["query"],
        group="search",
    ),
    AgentTool(
        name="search.deep",
        description=(
            "Thorough web search: rewrites the query several ways, queries every "
            "configured engine, reads the top pages and reranks them, returning "
            "full page text. Takes several seconds — use it for hard or ambiguous "
            "questions where search.web came back thin, not as the default."
        ),
        handler=_deep,
        parameters={
            "query": {"type": "string", "description": "What to research"},
            "limit": _LIMIT_PARAM,
            "site": _SITE_PARAM,
            "freshness": _FRESHNESS_PARAM,
        },
        required=["query"],
        group="search",
    ),
    AgentTool(
        name="search.read",
        description=(
            "Read one URL as extracted article text (boilerplate stripped, cached). "
            "Use it on results from search.web, or on any URL the user gives you."
        ),
        handler=_read,
        parameters={"url": {"type": "string", "description": "The page to read"}},
        required=["url"],
        group="search",
    ),
    AgentTool(
        name="search.index",
        description=(
            "Search this node's own crawled index of ML sites, blogs and API docs. "
            "Instant and free, but it only covers seeded sites — an empty result "
            "means 'not in the index', never 'not on the web'. Try it before "
            "search.web for library/API/framework documentation questions."
        ),
        handler=_index,
        parameters={
            "query": {"type": "string", "description": "What to look for"},
            "limit": _LIMIT_PARAM,
            "site": _SITE_PARAM,
        },
        required=["query"],
        group="search",
    ),
    AgentTool(
        name="search.crawl",
        description=(
            "Re-crawl a configured seed site into the local index. Runs for minutes "
            "in the background and blocks other queued work — only do this when the "
            "user asks to refresh the index."
        ),
        handler=_crawl,
        parameters={
            "seed_id": {"type": "string", "description": "Which seed to crawl"},
            "force": {
                "type": "boolean",
                "description": "Re-index unchanged pages too (default false)",
            },
        },
        required=["seed_id"],
        side_effect=True,
        specifier_template="{seed_id}",
        group="search",
    ),
]


def register_search_tools() -> None:
    """Insert the search backend tools into the sdk registry, register the built-in
    providers, and expose the `search` connector (called from app.py)."""
    from backend.modules.search.connector import build as build_connector
    from backend.modules.search.providers import register_builtin_providers

    register_builtin_providers()
    connector = build_connector()
    registry.connectors[connector.id] = connector
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
