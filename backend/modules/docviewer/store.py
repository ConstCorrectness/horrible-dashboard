"""The doc-set catalog: `docviewer_sets` and `docviewer_pages` in `app.db`.

Two tables rather than leaning on `library_sources` alone, because a doc set has
structure a flat source list cannot carry: the tree the sidebar renders, the crawl
depth a page was found at, and the order pages were discovered in. The library still
holds the *text* (each captured page is ingested as a `page` source, which is what
makes a set searchable); this is the shape.

**Page ids are deterministic** — `sha256(set_id + url)` — and that is load-bearing
rather than tidy. Intra-set links are rewritten at capture time to point at
`/api/docviewer/pages/<id>/content`, and at that moment the target page may not have
been captured yet. An artifact id could not be used: the store is content-addressed,
so a page's id depends on its bytes, which depend on the links it contains, which
depend on the ids of pages that link back to it. Deriving the id from the URL breaks
that circle.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir


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


def page_id(set_id: str, url: str) -> str:
    """The stable address of a page within a set. See the module docstring."""
    digest = hashlib.sha256(f"{set_id}\n{url}".encode("utf-8")).hexdigest()
    return digest[:32]


def init_docviewer_db() -> None:
    """Create both tables (idempotent), mirroring `library.store.init_library_db`."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS docviewer_sets (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                seed_url TEXT NOT NULL,
                prefix TEXT NOT NULL,
                library TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                page_count INTEGER NOT NULL DEFAULT 0,
                max_pages INTEGER NOT NULL DEFAULT 200,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_crawled_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS docviewer_pages (
                id TEXT PRIMARY KEY,
                set_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                artifact_id TEXT,
                source_id TEXT,
                parent_id TEXT,
                depth INTEGER NOT NULL DEFAULT 0,
                ordinal INTEGER NOT NULL DEFAULT 0,
                bytes INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_docviewer_pages_set "
            "ON docviewer_pages(set_id, ordinal)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_docviewer_pages_url "
            "ON docviewer_pages(set_id, url)"
        )


# ---- sets ------------------------------------------------------------------


def _set_row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "title": r["title"],
        "seed_url": r["seed_url"],
        "prefix": r["prefix"],
        "library": r["library"],
        "status": r["status"],
        "error": r["error"],
        "page_count": r["page_count"],
        "max_pages": r["max_pages"],
        "created_at": str(r["created_at"]),
        "last_crawled_at": str(r["last_crawled_at"]) if r["last_crawled_at"] else None,
    }


def create_set(
    *,
    title: str,
    seed_url: str,
    prefix: str,
    library: str,
    max_pages: int,
) -> dict[str, Any]:
    init_docviewer_db()
    set_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO docviewer_sets
                (id, title, seed_url, prefix, library, status, max_pages)
            VALUES (?, ?, ?, ?, ?, 'queued', ?)
            """,
            (set_id, title, seed_url, prefix, library, max_pages),
        )
    row = get_set(set_id)
    assert row is not None  # just inserted
    return row


def get_set(set_id: str) -> dict[str, Any] | None:
    init_docviewer_db()
    with get_db_conn() as conn:
        r = conn.execute(
            "SELECT * FROM docviewer_sets WHERE id = ?", (set_id,)
        ).fetchone()
    return _set_row(r) if r else None


def list_sets() -> list[dict[str, Any]]:
    init_docviewer_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM docviewer_sets ORDER BY created_at DESC"
        ).fetchall()
    return [_set_row(r) for r in rows]


def set_status(set_id: str, status: str, *, error: str | None = None) -> None:
    with get_db_conn() as conn:
        if status == "ready":
            conn.execute(
                "UPDATE docviewer_sets "
                "SET status = ?, error = ?, last_crawled_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (status, error, set_id),
            )
        else:
            conn.execute(
                "UPDATE docviewer_sets SET status = ?, error = ? WHERE id = ?",
                (status, error, set_id),
            )


def refresh_page_count(set_id: str) -> int:
    with get_db_conn() as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM docviewer_pages "
            "WHERE set_id = ? AND status = 'captured'",
            (set_id,),
        ).fetchone()
        conn.execute(
            "UPDATE docviewer_sets SET page_count = ? WHERE id = ?", (count, set_id)
        )
    return int(count)


def delete_set(set_id: str) -> bool:
    """Drop a set and its page rows.

    Artifacts and library sources are **not** touched here — the routes delete those
    explicitly, because both are shared stores and a silent cascade into them from a
    catalog helper is how unrelated data disappears.
    """
    init_docviewer_db()
    with get_db_conn() as conn:
        conn.execute("DELETE FROM docviewer_pages WHERE set_id = ?", (set_id,))
        cur = conn.execute("DELETE FROM docviewer_sets WHERE id = ?", (set_id,))
    return cur.rowcount > 0


# ---- pages -----------------------------------------------------------------


def _page_row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "set_id": r["set_id"],
        "url": r["url"],
        "title": r["title"],
        "status": r["status"],
        "error": r["error"],
        "artifact_id": r["artifact_id"],
        "source_id": r["source_id"],
        "parent_id": r["parent_id"],
        "depth": r["depth"],
        "ordinal": r["ordinal"],
        "bytes": r["bytes"],
    }


def upsert_page(
    *,
    set_id: str,
    url: str,
    title: str,
    status: str,
    canonical: str | None = None,
    parent_id: str | None = None,
    depth: int = 0,
    ordinal: int = 0,
) -> dict[str, Any]:
    """Record a page as pending (or refresh its title on a re-crawl).

    `url` is the URL actually fetched; `canonical` is its identity. They differ
    because `canonical_url` folds `http`/`https` and drops `www.` — fine for a key,
    wrong for a fetch. The id comes from the canonical form so two spellings of one
    page collapse to one row; the row keeps the spelling we successfully loaded.
    """
    init_docviewer_db()
    pid = page_id(set_id, canonical or url)
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO docviewer_pages
                (id, set_id, url, title, status, parent_id, depth, ordinal)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                status = excluded.status,
                parent_id = excluded.parent_id,
                depth = excluded.depth,
                ordinal = excluded.ordinal
            """,
            (pid, set_id, url, title, status, parent_id, depth, ordinal),
        )
    row = get_page(pid)
    assert row is not None  # just inserted
    return row


def mark_captured(
    page_key: str,
    *,
    title: str,
    artifact_id: str,
    source_id: str | None,
    size: int,
) -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            UPDATE docviewer_pages
            SET status = 'captured', title = ?, artifact_id = ?, source_id = ?,
                bytes = ?, error = NULL
            WHERE id = ?
            """,
            (title, artifact_id, source_id, size, page_key),
        )


def mark_failed(page_key: str, error: str) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE docviewer_pages SET status = 'failed', error = ? WHERE id = ?",
            (error[:500], page_key),
        )


def get_page(page_key: str) -> dict[str, Any] | None:
    init_docviewer_db()
    with get_db_conn() as conn:
        r = conn.execute(
            "SELECT * FROM docviewer_pages WHERE id = ?", (page_key,)
        ).fetchone()
    return _page_row(r) if r else None


def list_pages(set_id: str) -> list[dict[str, Any]]:
    init_docviewer_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM docviewer_pages WHERE set_id = ? ORDER BY ordinal, url",
            (set_id,),
        ).fetchall()
    return [_page_row(r) for r in rows]


def page_by_source(source_id: str) -> dict[str, Any] | None:
    init_docviewer_db()
    with get_db_conn() as conn:
        r = conn.execute(
            "SELECT * FROM docviewer_pages WHERE source_id = ?", (source_id,)
        ).fetchone()
    return _page_row(r) if r else None
