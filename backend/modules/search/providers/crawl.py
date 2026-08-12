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
import re
from dataclasses import replace
from typing import Any

from backend.modules.search.base import SearchProviderError, SearchResult
from backend.modules.search.crawl import index as webindex
from backend.modules.search.crawl.versions import installed_versions, version_series

logger = logging.getLogger(__name__)

# Chunks fetched per page-level result. A long docs page contributes many chunks and
# we only keep its best, so the raw limit has to overshoot.
_CHUNK_OVERSAMPLE = 4

# How much a hit loses for describing a release the machine doesn't have. Small on
# purpose — see `demote_mismatches`.
_MISMATCH_DEMOTION = 0.85


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

    return annotate_versions(_group_by_page(rows, limit=limit, site=site))


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
            raw={
                "seed_id": meta.get("seed_id"),
                "tags": meta.get("tags") or [],
                # Absent when the seed declares no package — "unversioned" and
                # "version unknown" are the same thing here, and neither is "".
                "version": meta.get("version") or None,
            },
        )

    ordered = sorted(best.values(), key=lambda r: r.score or 0.0, reverse=True)
    return ordered[:limit]


def demote_mismatches(
    results: list[SearchResult], installed: dict[str, str]
) -> list[SearchResult]:
    """Re-rank so docs for a release you don't have sit below ones you do.

    `installed` maps seed id → the version found on this machine. Pure, so the ranking
    rule is testable without touching a venv.

    Demotion rather than exclusion, and a small one: docs for an adjacent release are
    usually still right, and the failure this guards against is a *confident* wrong
    answer, not the presence of an older page. Fusion downstream is rank-based, so
    changing the order within this provider's list is the whole of the lever —
    the number itself never leaves here.
    """
    scored: list[tuple[float, int, SearchResult]] = []
    for position, result in enumerate(results):
        found = installed.get(str(result.raw.get("seed_id") or ""))
        mismatched = bool(
            found
            and result.raw.get("version")
            and version_series(str(result.raw["version"])) != version_series(found)
        )
        raw = {**result.raw, "installed": found or None, "version_mismatch": mismatched}
        base = result.score or 0.0
        scored.append(
            (
                base * _MISMATCH_DEMOTION if mismatched else base,
                position,
                replace(result, raw=raw),
            )
        )

    # `position` breaks ties by the original order, so an unversioned corpus of equal
    # scores can't be shuffled arbitrarily between calls.
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [result for _score, _pos, result in scored]


def annotate_versions(results: list[SearchResult]) -> list[SearchResult]:
    """Attach installed-version context to crawl hits and re-rank on it.

    Reads seed rows and on-disk distribution metadata, so it is kept separate from
    the pure grouping and ranking above. Never raises: version awareness improves a
    result list, and losing it must not cost the list.
    """
    if not results:
        return results
    try:
        return demote_mismatches(results, _installed_for(results))
    except Exception:  # noqa: BLE001
        logger.exception("couldn't resolve installed versions for crawl hits")
        return results


def _installed_for(results: list[SearchResult]) -> dict[str, str]:
    """seed id → the version of that seed's package installed on this machine."""
    from backend.modules.search.crawl import store
    from backend.modules.search.crawl.versions import parse_package

    out: dict[str, str] = {}
    for seed_id in {str(r.raw.get("seed_id") or "") for r in results} - {""}:
        seed = store.get_seed(seed_id)
        ref = parse_package((seed or {}).get("config", {}).get("package"))
        if ref is None:
            continue
        found = installed_versions(ref.dist_name)
        if found:
            # A package installed in several project venvs at different versions has
            # no single right answer, so the newest wins: it is the one a fresh
            # project would get, and picking the oldest would demote current docs.
            out[seed_id] = max(found.values(), key=_sort_key)
    return out


def _sort_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:4]) or (0,)
