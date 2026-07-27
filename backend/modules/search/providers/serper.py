"""Serper — Google's SERP, cheapest per query, no content extraction.

The economics are why it's here: search and extraction are separable, and paying a
premium for bundled extraction is wasteful when the pipeline already extracts through
its own SSRF-guarded fetcher. Serper for discovery + our own fetch is markedly
cheaper than an all-in-one provider at the same volume.
"""

from __future__ import annotations

from typing import Any

from backend.modules.search import credentials
from backend.modules.search.base import SearchProviderError, SearchResult
from backend.modules.search.providers._api import clean, request_json

API_URL = "https://google.serper.dev/search"

# Google's `tbs` recency operators, which Serper passes straight through.
_FRESHNESS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


class SerperProvider:
    id = "serper"
    label = "Serper"
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
            raise SearchProviderError("serper: no API key")

        body: dict[str, Any] = {
            "q": f"{query} site:{site}" if site else query,
            "num": max(1, min(limit, 20)),
        }
        if tbs := _FRESHNESS.get(freshness or ""):
            body["tbs"] = tbs

        data = await request_json(
            "POST",
            API_URL,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json_body=body,
            provider=self.id,
        )
        return parse_response(data)


def parse_response(data: Any) -> list[SearchResult]:
    """Pure parser.

    `answerBox` is promoted to the front when present: it's Google's own extracted
    answer, and for factual queries it is usually the best single result. Knowledge-
    graph and "people also ask" blocks are ignored — they're navigational, not
    sources, and a URL we can't cite is noise to a research agent.
    """
    results: list[SearchResult] = []
    payload = data or {}

    box = payload.get("answerBox") or {}
    if isinstance(box, dict) and box.get("link"):
        results.append(
            SearchResult(
                url=str(box["link"]),
                title=clean(box.get("title"), limit=300) or str(box["link"]),
                snippet=clean(box.get("snippet") or box.get("answer")),
                provider="serper",
                raw=box,
            )
        )

    for item in payload.get("organic", []) or []:
        url = str(item.get("link") or "").strip()
        if not url:
            continue
        results.append(
            SearchResult(
                url=url,
                title=clean(item.get("title"), limit=300) or url,
                snippet=clean(item.get("snippet")),
                published=item.get("date") or None,
                provider="serper",
                raw=item if isinstance(item, dict) else {},
            )
        )
    return results
