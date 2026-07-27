"""This node's own focused crawl index, exposed as a search provider.

Making the local index a `SearchProvider` rather than a separate search path is the
whole trick: it means crawl hits and live web hits arrive in the same shape, go into
the same rank fusion, and a page that both our crawl *and* Brave surfaced ends up
ranked above one that only appeared once. No special-casing anywhere downstream.

It is genuinely different in character from the metered providers, and the tool
descriptions say so: instant, free, rate-limit-free, and blind to everything outside
the seed list. A subagent should reach for it before burning a metered call, and
should never treat "not in the index" as "not on the web".
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.search.base import SearchProviderError, SearchResult
from backend.modules.search.crawl import index as webindex

logger = logging.getLogger(__name__)

# Chunks fetched per page-level result. A long docs page contributes many chunks and
# we only keep its best, so the raw limit has to overshoot.
_CHUNK_OVERSAMPLE = 4


class CrawlProvider:
    id = "crawl"
    label = "Focused index"
    needs_key = False

    def configured(self) -> bool:
        return webindex.has_content()

    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        site: str | None = None,
        freshness: str | None = None,
    ) -> list[SearchResult]:
        return await search_index(query, limit=limit, site=site)


async def search_index(
    query: str, *, limit: int = 8, site: str | None = None
) -> list[SearchResult]:
    """Semantic search over `webindex`, grouped from chunks up to pages.

    `freshness` is ignored: the index holds whatever the last crawl saw, and a
    recency filter over that would imply a currency the corpus doesn't have.
    """
    from backend.modules.database.embeddings import get_embedding
    from backend.modules.database.vectorstore import init_db, search_documents

    if not query.strip():
        return []

    init_db()
    embedding, method = await get_embedding(query)

    # The query must be embedded by the same model the index was built with, or the
    # vectors live in different spaces and the "nearest" results are arbitrary.
    if webindex.is_fallback(method):
        raise SearchProviderError(
            "crawl: the embedding provider is unavailable, so the query can't be "
            "placed in the index's vector space"
        )
    expected = webindex.index_dim()
    if expected is not None and len(embedding) != expected:
        raise SearchProviderError(
            f"crawl: the index holds {expected}-dim vectors but the current "
            f"embedding model produces {len(embedding)}. Reindex the crawl."
        )

    try:
        rows = search_documents(
            webindex.COLLECTION, embedding, max(1, limit) * _CHUNK_OVERSAMPLE
        )
    except Exception as exc:  # noqa: BLE001 — a broken index is one dead provider
        raise SearchProviderError(f"crawl: {exc}") from exc

    return _group_by_page(rows, limit=limit, site=site)


def _group_by_page(
    rows: list[dict[str, Any]], *, limit: int, site: str | None
) -> list[SearchResult]:
    """Collapse chunk hits into page results, keeping each page's best chunk.

    Pure, so the grouping rule is testable without a vector store. The rule matches
    the library's: only the *best* rank a page achieves counts — a page with six
    matching chunks isn't six times more relevant than one with a single strong
    chunk, and letting it place six times would crowd the result list.
    """
    from backend.modules.search.canonical import host_of

    best: dict[str, SearchResult] = {}
    for row in rows:
        meta = row.get("metadata") or {}
        url = str(meta.get("url") or "").strip()
        if not url:
            continue
        if site and host_of(url) != site.lower().removeprefix("www."):
            continue
        score = float(row.get("score") or 0.0)
        existing = best.get(url)
        if existing is not None and (existing.score or 0.0) >= score:
            continue
        best[url] = SearchResult(
            url=url,
            title=str(meta.get("title") or url)[:300],
            snippet=" ".join(str(row.get("text") or "").split())[:400],
            score=score,
            published=meta.get("crawled_at") or None,
            provider="crawl",
            raw={"seed_id": meta.get("seed_id"), "tags": meta.get("tags") or []},
        )

    ordered = sorted(best.values(), key=lambda r: r.score or 0.0, reverse=True)
    return ordered[:limit]
