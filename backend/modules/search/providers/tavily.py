"""Tavily — a search API built for agents rather than for humans.

Tavily does its own relevance filtering and returns cleaned page content inline, so
`include_raw_content` can save the pipeline a separate fetch on the results it
already trusts. That's what makes it the strongest default when a key is present,
and also the most expensive per query.
"""

from __future__ import annotations

from typing import Any

from backend.modules.search import credentials
from backend.modules.search.base import SearchProviderError, SearchResult
from backend.modules.search.providers._api import clean, request_json

API_URL = "https://api.tavily.com/search"

# Tavily takes a day count rather than a named window.
_FRESHNESS_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


class TavilyProvider:
    id = "tavily"
    label = "Tavily"
    needs_key = True

    def configured(self) -> bool:
        return bool(credentials.get_key(self.id))

    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        site: str | None = None,
        freshness: str | None = None,
    ) -> list[SearchResult]:
        key = credentials.get_key(self.id)
        if not key:
            raise SearchProviderError("tavily: no API key")

        body: dict[str, Any] = {
            "query": query,
            "max_results": max(1, min(limit, 20)),
            "search_depth": "basic",
        }
        if site:
            body["include_domains"] = [site]
        if days := _FRESHNESS_DAYS.get(freshness or ""):
            body["days"] = days

        data = await request_json(
            "POST",
            API_URL,
            headers={"Authorization": f"Bearer {key}"},
            json_body=body,
            provider=self.id,
        )
        return parse_response(data)


def parse_response(data: Any) -> list[SearchResult]:
    """Pure parser, so the response shape is testable against a recorded fixture
    without a key or a network."""
    results: list[SearchResult] = []
    for item in (data or {}).get("results", []) or []:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        results.append(
            SearchResult(
                url=url,
                title=clean(item.get("title"), limit=300) or url,
                snippet=clean(item.get("content")),
                score=float(item["score"]) if item.get("score") is not None else None,
                published=item.get("published_date") or None,
                provider="tavily",
                raw=item if isinstance(item, dict) else {},
            )
        )
    return results
