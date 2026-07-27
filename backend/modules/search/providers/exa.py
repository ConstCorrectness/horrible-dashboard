"""Exa — embedding-based ("neural") search rather than keyword matching.

Exa is the odd one in the fan-out and that's the point: it answers "find me pages
*like* this idea" where keyword engines answer "find me pages containing these
words". `type: "auto"` lets Exa pick per query, which beats forcing either mode.

Contents are deliberately not requested. Exa bills separately for text extraction,
and the pipeline already fetches the top results through the SSRF guard for free.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.modules.search import credentials
from backend.modules.search.base import SearchProviderError, SearchResult
from backend.modules.search.providers._api import clean, request_json

API_URL = "https://api.exa.ai/search"

_FRESHNESS_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


class ExaProvider:
    id = "exa"
    label = "Exa"
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
            raise SearchProviderError("exa: no API key")

        body: dict[str, Any] = {
            "query": query,
            "numResults": max(1, min(limit, 25)),
            "type": "auto",
        }
        if site:
            body["includeDomains"] = [site]
        if days := _FRESHNESS_DAYS.get(freshness or ""):
            since = datetime.now(timezone.utc) - timedelta(days=days)
            body["startPublishedDate"] = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        data = await request_json(
            "POST",
            API_URL,
            headers={"x-api-key": key, "Content-Type": "application/json"},
            json_body=body,
            provider=self.id,
        )
        return parse_response(data)


def parse_response(data: Any) -> list[SearchResult]:
    """Pure parser. Exa returns a `text` field only when contents were requested, so
    the snippet falls back to the summary/highlights it does include."""
    results: list[SearchResult] = []
    for item in (data or {}).get("results", []) or []:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        highlights = item.get("highlights") or []
        snippet = (
            item.get("summary")
            or (highlights[0] if highlights else "")
            or item.get("text")
            or ""
        )
        results.append(
            SearchResult(
                url=url,
                title=clean(item.get("title"), limit=300) or url,
                snippet=clean(snippet),
                score=float(item["score"]) if item.get("score") is not None else None,
                published=item.get("publishedDate") or None,
                provider="exa",
                raw=item if isinstance(item, dict) else {},
            )
        )
    return results
