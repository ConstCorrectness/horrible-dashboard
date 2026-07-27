"""Two caches in `app.db`: provider responses and extracted page text.

**Cached at the provider-call level, not the pipeline level.** The key is
`(provider, normalized query, limit, site, freshness)`, which is what makes the
expensive parts cheap: a 3× query fan-out overlaps heavily between rewrites, several
research subagents independently reach for the same obvious query, and a retried run
re-asks everything it asked before. Caching whole pipeline results instead would miss
all of that, because no two pipeline invocations share every parameter.

**The page cache is the bigger win.** Today every deep-research subagent re-fetches
the same arXiv abstract and the same canonical blog post with no dedupe at all —
each one a full HTTP round-trip plus a trafilatura parse.

Both are pure caches: a miss is always correct, an eviction is never a bug, and any
failure to read or write one degrades to doing the work. Purge-on-write keeps them
bounded without a sweeper task.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir

logger = logging.getLogger(__name__)

# Page text is capped before storage: a cache row is a convenience, not an archive
# (that's what the library is for).
_MAX_PAGE_CHARS = 200_000


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(ensure_app_db_dir()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_cache_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_cache (
                key        TEXT PRIMARY KEY,
                provider   TEXT NOT NULL,
                query      TEXT NOT NULL,
                results    TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_cache_created "
            "ON search_cache(created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_page_cache (
                url        TEXT PRIMARY KEY,
                final_url  TEXT,
                title      TEXT,
                author     TEXT,
                text       TEXT NOT NULL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_page_cache_fetched "
            "ON search_page_cache(fetched_at)"
        )


def _ttl_minutes() -> int:
    from backend.modules.settings.routes import get_value

    return max(0, int(get_value("search.cacheTtlMinutes", 60) or 0))


def _page_ttl_hours() -> int:
    from backend.modules.settings.routes import get_value

    return max(0, int(get_value("search.pageCacheHours", 24) or 0))


def result_key(
    provider: str,
    query: str,
    *,
    limit: int,
    site: str | None,
    freshness: str | None,
) -> str:
    """A stable cache key. The query is whitespace-normalized and lowercased so
    trivial restatements share a row — anything more aggressive would start merging
    queries that genuinely differ."""
    normalized = " ".join(query.split()).lower()
    raw = f"{provider}|{normalized}|{limit}|{site or ''}|{freshness or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_results(key: str) -> list[dict[str, Any]] | None:
    """Cached provider results, or None on a miss, an expiry, or any failure."""
    ttl = _ttl_minutes()
    if ttl <= 0:
        return None
    try:
        with get_db_conn() as conn:
            row = conn.execute(
                "SELECT results FROM search_cache WHERE key = ? "
                "AND created_at > datetime('now', ?)",
                (key, f"-{ttl} minutes"),
            ).fetchone()
    except sqlite3.Error:
        logger.exception("search cache read failed")
        return None
    if row is None:
        return None
    try:
        return json.loads(row["results"])
    except ValueError:
        return None


def put_results(
    key: str, provider: str, query: str, results: list[dict[str, Any]]
) -> None:
    """Store provider results and purge anything past the TTL."""
    ttl = _ttl_minutes()
    if ttl <= 0:
        return
    try:
        with get_db_conn() as conn:
            conn.execute(
                "INSERT INTO search_cache (key, provider, query, results, created_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET results = excluded.results, "
                "created_at = CURRENT_TIMESTAMP",
                (key, provider, query, json.dumps(results)),
            )
            conn.execute(
                "DELETE FROM search_cache WHERE created_at <= datetime('now', ?)",
                (f"-{max(ttl * 4, 1440)} minutes",),
            )
    except sqlite3.Error:
        logger.exception("search cache write failed")


@dataclass(frozen=True)
class CachedPage:
    url: str
    final_url: str
    title: str
    author: str
    text: str


def get_page(canonical: str) -> CachedPage | None:
    hours = _page_ttl_hours()
    if hours <= 0:
        return None
    try:
        with get_db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM search_page_cache WHERE url = ? "
                "AND fetched_at > datetime('now', ?)",
                (canonical, f"-{hours} hours"),
            ).fetchone()
    except sqlite3.Error:
        logger.exception("page cache read failed")
        return None
    if row is None:
        return None
    return CachedPage(
        url=row["url"],
        final_url=row["final_url"] or row["url"],
        title=row["title"] or "",
        author=row["author"] or "",
        text=row["text"] or "",
    )


def put_page(
    canonical: str, *, final_url: str, title: str, author: str, text: str
) -> None:
    hours = _page_ttl_hours()
    if hours <= 0:
        return
    try:
        with get_db_conn() as conn:
            conn.execute(
                "INSERT INTO search_page_cache "
                "(url, final_url, title, author, text, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(url) DO UPDATE SET final_url = excluded.final_url, "
                "title = excluded.title, author = excluded.author, "
                "text = excluded.text, fetched_at = CURRENT_TIMESTAMP",
                (canonical, final_url, title, author, text[:_MAX_PAGE_CHARS]),
            )
            conn.execute(
                "DELETE FROM search_page_cache WHERE fetched_at <= datetime('now', ?)",
                (f"-{max(hours * 4, 168)} hours",),
            )
    except sqlite3.Error:
        logger.exception("page cache write failed")


def clear() -> dict[str, int]:
    """Empty both caches. Returns how many rows went."""
    with get_db_conn() as conn:
        results = conn.execute("DELETE FROM search_cache").rowcount
        pages = conn.execute("DELETE FROM search_page_cache").rowcount
    return {"results": max(results, 0), "pages": max(pages, 0)}
