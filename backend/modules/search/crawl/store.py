"""Crawl bookkeeping: `crawl_seeds` and `crawl_pages` in `app.db`.

`crawl_pages` is the reason a re-crawl is cheap. It mirrors the discipline of the
Google Drive sync's `google_drive_files` map — remember enough about what you already
have that the next pass *replaces* rather than duplicates — with one addition: web
pages carry no `modifiedTime`, so the durable identity of a page's content is a hash
of its extracted text. That gives three escalating levels of "nothing to do":

1. the server answers **304** to our `If-None-Match`/`If-Modified-Since` — no body
   transferred, no extraction, no embedding;
2. the body came back but its **content hash is unchanged** — no embedding, which is
   the expensive half;
3. the content changed — only then do we pay for chunking, embedding and a write.

On a second crawl of a docs site almost everything lands in (1) or (2).

Seeds are split into built-in and user rows by a `builtin` flag rather than separate
tables: they behave identically once loaded, and the flag only decides whether
"delete" means delete or disable.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir

logger = logging.getLogger(__name__)

_SEEDS_FILE = Path(__file__).parent / "seeds.json"

# `corpus` marks the llms-full.txt bookkeeping row rather than a page: it carries the
# etag that makes a whole-seed 304 possible, and is deliberately excluded from
# `known_urls` and the page count so it can never be crawled or counted as content.
PAGE_STATUSES = ("ok", "error", "gone", "corpus")


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


def init_crawl_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_seeds (
                id              TEXT PRIMARY KEY,
                label           TEXT NOT NULL,
                config          TEXT NOT NULL,
                enabled         INTEGER NOT NULL DEFAULT 1,
                builtin         INTEGER NOT NULL DEFAULT 0,
                last_crawled_at TIMESTAMP,
                last_status     TEXT,
                last_error      TEXT,
                pages           INTEGER NOT NULL DEFAULT 0,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_pages (
                url           TEXT NOT NULL,
                seed_id       TEXT NOT NULL,
                title         TEXT,
                etag          TEXT,
                last_modified TEXT,
                content_hash  TEXT NOT NULL,
                chunk_count   INTEGER NOT NULL DEFAULT 0,
                status        TEXT NOT NULL DEFAULT 'ok',
                error         TEXT,
                fetched_at    TIMESTAMP,
                indexed_at    TIMESTAMP,
                PRIMARY KEY (url, seed_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_crawl_pages_seed ON crawl_pages(seed_id)"
        )
        # Both statements above are CREATE TABLE IF NOT EXISTS, so an existing install
        # never sees a column added after its tables were made. Every later column
        # goes here instead.
        _ensure_column(conn, "crawl_seeds", "version", "TEXT")
        _ensure_column(conn, "crawl_pages", "version", "TEXT")
    _load_builtin_seeds()


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, decl: str
) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _load_builtin_seeds() -> None:
    """Insert the shipped seed list once.

    Existing rows are left alone on purpose: a user who narrowed `max_pages` or
    disabled a seed should not have that undone every time the app restarts. New
    built-ins added in a later release still appear, because the insert is per-id.
    """
    try:
        specs = json.loads(_SEEDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("couldn't read built-in crawl seeds at %s", _SEEDS_FILE)
        return
    with get_db_conn() as conn:
        for spec in specs:
            conn.execute(
                "INSERT OR IGNORE INTO crawl_seeds (id, label, config, builtin) "
                "VALUES (?, ?, ?, 1)",
                (
                    str(spec["id"]),
                    str(spec.get("label") or spec["id"]),
                    json.dumps(spec),
                ),
            )


def _seed_row(r: Any) -> dict[str, Any]:
    try:
        config = json.loads(r["config"])
    except ValueError:
        config = {}
    return {
        "id": r["id"],
        "label": r["label"],
        "config": config,
        "enabled": bool(r["enabled"]),
        "builtin": bool(r["builtin"]),
        "last_crawled_at": r["last_crawled_at"],
        "last_status": r["last_status"],
        "last_error": r["last_error"],
        "pages": r["pages"],
        "version": r["version"],
    }


def list_seeds() -> list[dict[str, Any]]:
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM crawl_seeds ORDER BY builtin DESC, label COLLATE NOCASE"
        ).fetchall()
    return [_seed_row(r) for r in rows]


def get_seed(seed_id: str) -> dict[str, Any] | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM crawl_seeds WHERE id = ?", (seed_id,)
        ).fetchone()
    return _seed_row(row) if row else None


def upsert_seed(spec: dict[str, Any], *, builtin: bool = False) -> dict[str, Any]:
    seed_id = str(spec["id"]).strip()
    label = str(spec.get("label") or seed_id)
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO crawl_seeds (id, label, config, builtin) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET label = excluded.label, "
            "config = excluded.config",
            (seed_id, label, json.dumps(spec), 1 if builtin else 0),
        )
    seed = get_seed(seed_id)
    assert seed is not None  # just written
    return seed


def set_enabled(seed_id: str, enabled: bool) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE crawl_seeds SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, seed_id),
        )


def delete_seed(seed_id: str) -> str:
    """Remove a user seed, or disable a built-in one.

    A built-in row would come straight back on the next start, so deleting it would
    read as "the app ignored me". Disabling is the honest equivalent.
    """
    seed = get_seed(seed_id)
    if seed is None:
        return "missing"
    if seed["builtin"]:
        set_enabled(seed_id, False)
        return "disabled"
    with get_db_conn() as conn:
        conn.execute("DELETE FROM crawl_seeds WHERE id = ?", (seed_id,))
        conn.execute("DELETE FROM crawl_pages WHERE seed_id = ?", (seed_id,))
    return "deleted"


def set_seed_version(seed_id: str, version: str | None) -> None:
    """Record the release the seed's pages now describe.

    Written at the *start* of a crawl rather than the end: the pages being written
    during the run are stamped with it, so a run that dies halfway must not leave the
    seed claiming the old version for pages that already hold the new one.
    """
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE crawl_seeds SET version = ? WHERE id = ?",
            (version or None, seed_id),
        )


def finish_seed(seed_id: str, *, status: str, error: str | None, pages: int) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE crawl_seeds SET last_crawled_at = CURRENT_TIMESTAMP, "
            "last_status = ?, last_error = ?, pages = ? WHERE id = ?",
            (status, error, pages, seed_id),
        )


def due_seeds() -> list[dict[str, Any]]:
    """Enabled seeds never crawled, or older than their own `recrawl_days`."""
    out: list[dict[str, Any]] = []
    with get_db_conn() as conn:
        for row in conn.execute(
            "SELECT * FROM crawl_seeds WHERE enabled = 1"
        ).fetchall():
            seed = _seed_row(row)
            days = int(seed["config"].get("recrawl_days") or 14)
            if seed["last_crawled_at"] is None:
                out.append(seed)
                continue
            due = conn.execute(
                "SELECT ? <= datetime('now', ?) AS due",
                (seed["last_crawled_at"], f"-{max(days, 0)} days"),
            ).fetchone()
            if due and due["due"]:
                out.append(seed)
    return out


# --- pages ------------------------------------------------------------------


def get_page(url: str, seed_id: str) -> dict[str, Any] | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM crawl_pages WHERE url = ? AND seed_id = ?", (url, seed_id)
        ).fetchone()
    return dict(row) if row else None


def record_page(
    url: str,
    seed_id: str,
    *,
    title: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    content_hash: str = "",
    chunk_count: int = 0,
    status: str = "ok",
    error: str | None = None,
    indexed: bool = False,
    version: str | None = None,
) -> None:
    """Upsert what we now know about a page. `indexed` bumps `indexed_at` only when
    chunks were actually written, so "last seen" and "last embedded" stay distinct —
    that difference is what makes the skip paths auditable.

    `version` is only overwritten when the page was actually indexed: it describes the
    release the *chunks in the vector store* were written for, and an error row would
    otherwise claim a version whose text was never indexed.
    """
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO crawl_pages
                (url, seed_id, title, etag, last_modified, content_hash,
                 chunk_count, status, error, version, fetched_at, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
                    CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)
            ON CONFLICT(url, seed_id) DO UPDATE SET
                title = excluded.title,
                etag = COALESCE(excluded.etag, crawl_pages.etag),
                last_modified = COALESCE(excluded.last_modified,
                                         crawl_pages.last_modified),
                content_hash = excluded.content_hash,
                chunk_count = excluded.chunk_count,
                status = excluded.status,
                error = excluded.error,
                version = CASE WHEN ? THEN excluded.version
                               ELSE crawl_pages.version END,
                fetched_at = CURRENT_TIMESTAMP,
                indexed_at = CASE WHEN ? THEN CURRENT_TIMESTAMP
                                  ELSE crawl_pages.indexed_at END
            """,
            (
                url,
                seed_id,
                title,
                etag,
                last_modified,
                content_hash,
                chunk_count,
                status,
                error,
                version or None,
                1 if indexed else 0,
                1 if indexed else 0,
                1 if indexed else 0,
            ),
        )


def touch_page(url: str, seed_id: str, *, status: str = "ok") -> None:
    """Mark a page as seen-and-unchanged without disturbing its content hash.

    `status` is explicit because the corpus row must stay `corpus`: promoting it to
    `ok` on a 304 would put the llms-full.txt file itself into the next run's
    frontier and count it as an indexed page.
    """
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE crawl_pages SET fetched_at = CURRENT_TIMESTAMP, status = ?, "
            "error = NULL WHERE url = ? AND seed_id = ?",
            (status if status in PAGE_STATUSES else "ok", url, seed_id),
        )


def drop_page(url: str, seed_id: str) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "DELETE FROM crawl_pages WHERE url = ? AND seed_id = ?", (url, seed_id)
        )


def known_urls(seed_id: str) -> list[str]:
    """Every page this seed has successfully indexed before.

    Used to seed a re-crawl's frontier. Without it, a re-crawl discovers nothing:
    the start page answers **304**, so there is no HTML to extract links from, and
    the crawl ends having checked exactly one URL while the pages that actually
    change go unnoticed forever.
    """
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT url FROM crawl_pages WHERE seed_id = ? AND status = 'ok' "
            "ORDER BY indexed_at DESC",
            (seed_id,),
        ).fetchall()
    return [r["url"] for r in rows]


def seed_stats(seed_id: str) -> dict[str, int]:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS pages, COALESCE(SUM(chunk_count), 0) AS chunks "
            "FROM crawl_pages WHERE seed_id = ? AND status = 'ok'",
            (seed_id,),
        ).fetchone()
    return {"pages": int(row["pages"] or 0), "chunks": int(row["chunks"] or 0)}
