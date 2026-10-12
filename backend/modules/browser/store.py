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


# ---- bookmarks: a façade over the knowledge-base catalog -------------------------
#
# A bookmark is a starred `kb_items` row. The browser's routes and response shape are
# unchanged, so the bookmarks strip keeps working, but "bookmark this page" and "save
# this page to the knowledge base" are now the same record: a page starred in the
# browser shows up in the Knowledge pane, and a primer starred there shows up on the
# strip. `browser_bookmarks` rows were copied over once (ids kept) by
# `kb.store.init_kb_db`; the table itself is no longer read.


def _as_bookmark(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "url": item["url"] or "",
        "title": item["title"],
        "tags": item["tags"],
        "added_at": item["added_at"],
    }


def add_bookmark(url: str, title: str, tags: list[str]) -> dict[str, Any]:
    """Star `url` (idempotent: an existing item for the URL is starred and returned,
    keeping its own title and tags)."""
    from backend.modules.kb import store as kb_store

    existing = kb_store.get_by_url(url)
    if existing is not None:
        item = kb_store.update_item(existing["id"], starred=True) or existing
        return _as_bookmark(item)
    item = kb_store.create_item(
        url=url, title=title, tags=tags, kind="bookmark", starred=True
    )
    return _as_bookmark(item)


def list_bookmarks() -> list[dict[str, Any]]:
    from backend.modules.kb import store as kb_store

    rows, _total = kb_store.list_items(starred=True, limit=1000)
    return [_as_bookmark(r) for r in rows]


def delete_bookmark(bookmark_id: str) -> bool:
    """Un-star. A plain bookmark with nothing else to it is removed outright; an
    item the user has invested in (notes, status, indexed content, a place in a
    course) only loses its star — removing a bookmark must not delete a primer."""
    from backend.modules.kb import store as kb_store

    item = kb_store.get_item(bookmark_id)
    if item is None or not item["starred"]:
        return False
    disposable = (
        item["kind"] == "bookmark"
        and item["index_mode"] == "link"
        and not item["notes"]
        and not item["collections"]
        and item["parent_id"] is None
        and item["status"] == "to_read"
    )
    if disposable:
        kb_store.delete_item(bookmark_id)
    else:
        kb_store.update_item(bookmark_id, starred=False)
    return True
