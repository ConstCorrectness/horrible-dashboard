"""Server-side browser history + bookmarks — a small SQLite catalog so both
persist across reloads and machines.

Lives in the shared app DB (`.data/app.db`, the same file as the library catalog),
mirroring `library.store`. History is upserted per URL (one row per URL, bumped to
the latest visit) so it stays compact; bookmarks are deduped by URL.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from contextlib import contextmanager
import sqlite3
from typing import Generator

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


def init_browser_db() -> None:
    """Create the history + bookmarks tables (idempotent)."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS browser_history (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                visited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS browser_bookmarks (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def _history_row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "url": r["url"],
        "title": r["title"],
        "visited_at": str(r["visited_at"]),
    }


def _bookmark_row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "url": r["url"],
        "title": r["title"],
        "tags": json.loads(r["tags"] or "[]"),
        "added_at": str(r["added_at"]),
    }


def record_visit(url: str, title: str) -> dict[str, Any]:
    """Upsert a history entry for `url`, bumping its visit time to now."""
    init_browser_db()
    entry_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO browser_history (id, url, title, visited_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                visited_at = CURRENT_TIMESTAMP
            """,
            (entry_id, url, title),
        )
        r = conn.execute(
            "SELECT * FROM browser_history WHERE url = ?", (url,)
        ).fetchone()
    return _history_row(r)


def list_history(limit: int = 100) -> list[dict[str, Any]]:
    init_browser_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM browser_history ORDER BY visited_at DESC, id DESC LIMIT ?",
            (max(1, min(limit, 1000)),),
        ).fetchall()
    return [_history_row(r) for r in rows]


def clear_history() -> None:
    init_browser_db()
    with get_db_conn() as conn:
        conn.execute("DELETE FROM browser_history")


def add_bookmark(url: str, title: str, tags: list[str]) -> dict[str, Any]:
    """Bookmark `url` (idempotent: an existing bookmark for the URL is returned)."""
    init_browser_db()
    with get_db_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM browser_bookmarks WHERE url = ?", (url,)
        ).fetchone()
        if existing is not None:
            return _bookmark_row(existing)
        bookmark_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO browser_bookmarks (id, url, title, tags) VALUES (?, ?, ?, ?)",
            (bookmark_id, url, title, json.dumps(tags)),
        )
        r = conn.execute(
            "SELECT * FROM browser_bookmarks WHERE id = ?", (bookmark_id,)
        ).fetchone()
    return _bookmark_row(r)


def list_bookmarks() -> list[dict[str, Any]]:
    init_browser_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM browser_bookmarks ORDER BY added_at DESC, id DESC"
        ).fetchall()
    return [_bookmark_row(r) for r in rows]


def delete_bookmark(bookmark_id: str) -> bool:
    init_browser_db()
    with get_db_conn() as conn:
        cur = conn.execute("DELETE FROM browser_bookmarks WHERE id = ?", (bookmark_id,))
        return cur.rowcount > 0
