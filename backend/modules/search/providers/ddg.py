"""DuckDuckGo's lite-HTML endpoint, scraped. The zero-config last resort.

Moved here from `research/rtools.py`, where it was the *only* way anything in this
app found a URL. It keeps that job — with no API key and no SearXNG instance, this is
what makes search work out of the box — but it is now the fallback rather than the
mechanism.

It is a scrape of an HTML page, so it will drift and it will rate-limit. Both failure
modes are handled by returning nothing rather than raising garbage, and
`auto_provider_ids()` drops it from the fan-out as soon as any real provider is
configured, so a working setup never depends on it.

Unlike the vendor APIs this *does* go through `_fetch_guarded`: the endpoint is a
public website rather than an API we hold a contract with, and it costs nothing to
keep it on the guarded path.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from backend.modules.search.base import SearchProviderError, SearchResult
from backend.modules.search.providers._api import clean

logger = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"

_RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>', re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip(html: str) -> str:
    return " ".join(_TAG_RE.sub(" ", html).split())


def _href_to_url(href: str) -> str | None:
    """DDG wraps results as `/l/?uddg=<encoded>`; unwrap to the target URL."""
    if href.startswith("http") and "duckduckgo.com" not in href:
        return href
    parts = urlsplit(href)
    target = parse_qs(parts.query).get("uddg", [None])[0]
    return unquote(target) if target else None


def parse_ddg_results(html: str, limit: int = 8) -> list[dict[str, str]]:
    """Best-effort scrape of the DDG lite-HTML results page. Pure — testable.

    Kept returning plain dicts because `research`'s tests import it directly; the
    provider wraps them into `SearchResult`s.
    """
    results: list[dict[str, str]] = []
    snippets = [_strip(m.group("snippet")) for m in _SNIPPET_RE.finditer(html)]
    for i, match in enumerate(_RESULT_RE.finditer(html)):
        url = _href_to_url(match.group("href"))
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


class DdgProvider:
    id = "ddg"
    label = "DuckDuckGo (keyless)"
    needs_key = False

    def configured(self) -> bool:
        # Always available — no key, no URL, nothing to set up. That is the entire
        # reason it exists.
        return True

    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        site: str | None = None,
        freshness: str | None = None,
    ) -> list[SearchResult]:
        from backend.modules.browser.fetch import _fetch_guarded

        # DDG has no freshness parameter on the lite endpoint; `site:` works in-query.
        q = f"{query} site:{site}" if site else query
        url = f"{DDG_URL}?{urlencode({'q': q})}"
        try:
            _final, resp = await _fetch_guarded(
                url, accept=("html", "text"), max_bytes=2_000_000
            )
        except Exception as exc:  # noqa: BLE001 — rate limits and blocks are routine
            raise SearchProviderError(f"ddg: {exc}") from exc

        rows = parse_ddg_results(resp.text, limit=limit)
        if not rows:
            # Markup drift and a genuine zero-result query look identical from here.
            # Say so, rather than asserting the web is empty.
            logger.info("ddg returned no parseable results for %r", query)
        return [
            SearchResult(
                url=row["url"],
                title=clean(row["title"], limit=300) or row["url"],
                snippet=clean(row["snippet"]),
                provider="ddg",
            )
            for row in rows
        ]
