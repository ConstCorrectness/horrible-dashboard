"""SearXNG — a self-hosted metasearch instance. Keyless, private, no per-query cost.

This is the "no vendor" answer: SearXNG proxies your query to 70+ upstream engines
and aggregates the results, holding no index of its own. Point `search.searxngUrl` at
an instance and the fan-out gains a provider that costs nothing and leaks nothing to
a search vendor.

**Run your own.** Public instances rate-limit or 429 agent traffic almost
immediately — their bot limiter is doing exactly its job. One container:

    docker run -d -p 8888:8080 --name searxng searxng/searxng

**Then enable the JSON format**, which is off by default and is the single most
common reason this provider returns nothing: add `json` to `search.formats` in the
instance's `settings.yml` and restart it. A JSON-disabled instance answers HTML with
a 200, so the failure looks like "no results" rather than an error — `_parse` says so
explicitly instead of returning an empty list.

**Not bundled, deliberately.** SearXNG is AGPL-3.0 and a full uwsgi service; this
repo declines to vendor AGPL code (pypdf over PyMuPDF, SingleFile supported but never
shipped). Talking to an instance over HTTP raises no licensing question.

**Egress.** The base URL is trusted because it comes from a *setting*, so loopback is
allowed here and the SSRF guard is deliberately not applied — a local instance is the
normal deployment and the guard would reject it. This URL must never come from model
output. Result URLs are a different leg entirely and are always guarded.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from backend.modules.search.base import SearchProviderError, SearchResult
from backend.modules.search.providers._api import clean, request_json

logger = logging.getLogger(__name__)

_FRESHNESS = {"day": "day", "week": "week", "month": "month", "year": "year"}


def instance_url() -> str:
    """The configured instance, normalized without a trailing slash. Empty when unset
    or when the setting isn't a plausible http(s) URL."""
    from backend.modules.settings.routes import get_value

    raw = str(get_value("search.searxngUrl", "") or "").strip().rstrip("/")
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        logger.warning("search.searxngUrl is not an http(s) URL: %r", raw)
        return ""
    return raw


class SearxngProvider:
    id = "searxng"
    label = "SearXNG"
    needs_key = False

    def configured(self) -> bool:
        return bool(instance_url())

    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        site: str | None = None,
        freshness: str | None = None,
    ) -> list[SearchResult]:
        base = instance_url()
        if not base:
            raise SearchProviderError(
                "searxng: no instance URL (set search.searxngUrl)"
            )

        params: dict[str, Any] = {
            "q": f"{query} site:{site}" if site else query,
            "format": "json",
            "language": "en",
        }
        if window := _FRESHNESS.get(freshness or ""):
            params["time_range"] = window

        data = await request_json(
            "GET",
            f"{base}/search",
            headers={"Accept": "application/json"},
            params=params,
            provider=self.id,
        )
        return parse_response(data, limit=limit)


def parse_response(data: Any, *, limit: int = 8) -> list[SearchResult]:
    """Pure parser.

    A dict with no `results` key is the JSON-format-disabled case in disguise, so it
    raises with the fix rather than returning an empty list that reads as "the web
    has nothing about this".
    """
    if not isinstance(data, dict):
        raise SearchProviderError(
            "searxng: instance did not return JSON — add `json` to search.formats "
            "in its settings.yml and restart it"
        )
    if "results" not in data:
        raise SearchProviderError(
            "searxng: response has no `results` — the instance likely has the JSON "
            "format disabled (add `json` to search.formats in settings.yml)"
        )

    results: list[SearchResult] = []
    for item in data.get("results") or []:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        results.append(
            SearchResult(
                url=url,
                title=clean(item.get("title"), limit=300) or url,
                snippet=clean(item.get("content")),
                score=float(item["score"]) if item.get("score") is not None else None,
                published=item.get("publishedDate") or None,
                provider="searxng",
                raw=item if isinstance(item, dict) else {},
            )
        )
        if len(results) >= limit:
            break
    return results
