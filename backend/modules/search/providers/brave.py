"""Brave Search — the one mainstream API backed by its own index.

Worth knowing when choosing a provider: Serper and most "Google API" vendors resell
scraped Google results, which makes them a legal and availability dependency on
someone else's terms of service. Brave crawls its own, so its results genuinely
differ from everyone else's — which is also what makes it a good fan-out partner
rather than a redundant one.
"""

from __future__ import annotations

from typing import Any

from backend.modules.search import credentials
from backend.modules.search.base import SearchProviderError, SearchResult
from backend.modules.search.providers._api import clean, request_json

API_URL = "https://api.search.brave.com/res/v1/web/search"

# Brave's own recency vocabulary: past day/week/month/year.
_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


class BraveProvider:
    id = "brave"
    label = "Brave Search"
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
            raise SearchProviderError("brave: no API key")

        # Brave has no domain parameter; `site:` in the query is the documented way.
        q = f"{query} site:{site}" if site else query
        params: dict[str, Any] = {"q": q, "count": max(1, min(limit, 20))}
        if window := _FRESHNESS.get(freshness or ""):
            params["freshness"] = window

        data = await request_json(
            "GET",
            API_URL,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": key,
            },
            params=params,
            provider=self.id,
        )
        return parse_response(data)


def parse_response(data: Any) -> list[SearchResult]:
    """Pure parser. Brave nests the web results under `web.results`; other verticals
    (news, videos) sit beside it and are deliberately ignored."""
    results: list[SearchResult] = []
    for item in ((data or {}).get("web") or {}).get("results", []) or []:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        results.append(
            SearchResult(
                url=url,
                title=clean(item.get("title"), limit=300) or url,
                snippet=clean(item.get("description")),
                published=(item.get("page_age") or item.get("age")) or None,
                provider="brave",
                raw=item if isinstance(item, dict) else {},
            )
        )
    return results
