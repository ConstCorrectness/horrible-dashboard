"""The deep-research subagents' internal toolset.

Distinct from `agent_tools.py` (the *user's* agent tools, permission-gated,
registered in the sdk registry): these run inside a research run with no user in
the loop, so the set is fixed, read-mostly, and every network path rides the
SSRF guard. `save_source` is the one write — it files evidence into the run's
library, which is the point of a research node.

DuckDuckGo's HTML endpoint is the keyless web search. Its markup drifts and it
rate-limits; both failure modes degrade to an explanatory tool result (the
model routes around a broken tool far better than a crashed run).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from backend.modules.arxiv import client as arxiv_client
from backend.modules.browser.fetch import _fetch_guarded, fetch_readable
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_PAGE_TEXT_CAP = 8_000
_DDG_URL = "https://html.duckduckgo.com/html/"
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(
    r'class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>', re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip(html: str) -> str:
    return " ".join(_TAG_RE.sub(" ", html).split())


def _ddg_href_to_url(href: str) -> str | None:
    """DDG wraps results as /l/?uddg=<encoded>; unwrap to the target URL."""
    if href.startswith("http") and "duckduckgo.com" not in href:
        return href
    parts = urlsplit(href)
    target = parse_qs(parts.query).get("uddg", [None])[0]
    return unquote(target) if target else None


def parse_ddg_results(html: str, limit: int = 8) -> list[dict[str, str]]:
    """Best-effort scrape of the DDG lite-HTML results page. Pure — testable."""
    results: list[dict[str, str]] = []
    snippets = [_strip(m.group("snippet")) for m in _DDG_SNIPPET_RE.finditer(html)]
    for i, match in enumerate(_DDG_RESULT_RE.finditer(html)):
        url = _ddg_href_to_url(match.group("href"))
        if not url:
            continue
        results.append(
            {
                "title": _strip(match.group("title")),
                "url": url,
                "snippet": snippets[i] if i < len(snippets) else "",
            }
        )
        if len(results) >= limit:
            break
    return results


async def _web_search(args: dict[str, Any]) -> dict[str, Any]:
    if not bool(get_value("research.webSearch", True)):
        return {"error": "web search is disabled (research.webSearch setting)"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "web_search needs a query"}
    url = f"{_DDG_URL}?{urlencode({'q': query})}"
    try:
        _final, resp = await _fetch_guarded(
            url, accept=("html", "text"), max_bytes=2_000_000
        )
        results = parse_ddg_results(resp.text)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the run
        logger.warning("web_search failed: %s", exc)
        return {
            "error": f"web search unavailable ({exc}); use arxiv_search or fetch_page"
        }
    if not results:
        return {
            "results": [],
            "note": "no results parsed — the engine may have changed markup; "
            "try arxiv_search or fetch_page on a known site",
        }
    return {"results": results}


async def _fetch_page(args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url", "")).strip()
    if not url:
        return {"error": "fetch_page needs a url"}
    try:
        article = await fetch_readable(url)
    except Exception as exc:  # noqa: BLE001 — bad URLs are routine mid-run
        return {"error": f"couldn't read {url}: {exc}"}
    return {
        "url": article.url,
        "title": article.title,
        "author": article.author,
        "text": article.text[:_PAGE_TEXT_CAP],
        "truncated": len(article.text) > _PAGE_TEXT_CAP,
    }


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
            "Search the web (keyless). Returns titles, URLs, and snippets.",
            {"query": {"type": "string"}},
            ["query"],
        ),
        tool(
            "fetch_page",
            "Read a URL as extracted article text (capped).",
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
