"""The `library_sources` catalog — one row per ingested source.

A SQLite table in the app database (`$HORRIBLE_DATA_DIR/app.db`). Chunk *content*
and its vectors live in the vector store — LanceDB, under
`$HORRIBLE_DATA_DIR/lancedb`, one table per library (see `database/vectorstore.py`).
This table is the human-facing index: title, type, status, tags, chunk count, and —
for `image`/`video` sources — the `asset` descriptor pointing at media we reference
but never copy.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.database.vectorstore import list_documents

from contextlib import contextmanager
import sqlite3
from typing import Generator


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


def init_library_db() -> None:
    """Create the catalog table (idempotent), mirroring `vectorstore.init_db`."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS library_sources (
                id TEXT PRIMARY KEY,
                library TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                author TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL,
                error TEXT,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_library ON library_sources(library)"
        )
        # `asset` (JSON MediaAsset, null for blog/note) arrived after the table did,
        # so CREATE TABLE IF NOT EXISTS alone would leave existing databases without
        # it. Add it in place; SQLite has no ADD COLUMN IF NOT EXISTS, so probe first.
        columns = {
            r["name"] for r in conn.execute("PRAGMA table_info(library_sources)")
        }
        if "asset" not in columns:
            conn.execute("ALTER TABLE library_sources ADD COLUMN asset TEXT")
        # `artifact_id` (blob reference for page/pdf sources) arrived later still;
        # same probe-then-ALTER dance.
        if "artifact_id" not in columns:
            conn.execute("ALTER TABLE library_sources ADD COLUMN artifact_id TEXT")


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "library": r["library"],
        "type": r["type"],
        "title": r["title"],
        "url": r["url"],
        "author": r["author"],
        "tags": json.loads(r["tags"] or "[]"),
        "status": r["status"],
        "error": r["error"],
        "chunk_count": r["chunk_count"],
        "added_at": str(r["added_at"]),
        "asset": json.loads(r["asset"]) if r["asset"] else None,
        "artifact_id": r["artifact_id"],
    }


def create_source(
    *,
    library: str,
    type: str,
    title: str,
    url: str | None,
    author: str | None,
    tags: list[str],
    asset: dict[str, Any] | None = None,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    """Insert a new source in `queued` status and return it."""
    init_library_db()
    source_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO library_sources
                (id, library, type, title, url, author, tags, status, asset, artifact_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                source_id,
                library,
                type,
                title,
                url,
                author,
                json.dumps(tags),
                json.dumps(asset) if asset else None,
                artifact_id,
            ),
        )
    source = get_source(source_id)
    assert source is not None  # just inserted
    return source


def set_status(
    source_id: str,
    status: str,
    *,
    error: str | None = None,
    chunk_count: int | None = None,
) -> None:
    with get_db_conn() as conn:
        if chunk_count is None:
            conn.execute(
                "UPDATE library_sources SET status = ?, error = ? WHERE id = ?",
                (status, error, source_id),
            )
        else:
            conn.execute(
                "UPDATE library_sources SET status = ?, error = ?, chunk_count = ? "
                "WHERE id = ?",
                (status, error, chunk_count, source_id),
            )


def update_meta(
    source_id: str, *, title: str | None = None, author: str | None = None
) -> None:
    """Persist metadata discovered during extraction (e.g. a blog's real title)."""
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE library_sources SET title = COALESCE(?, title), "
            "author = COALESCE(?, author) WHERE id = ?",
            (title, author, source_id),
        )


def get_source(source_id: str) -> dict[str, Any] | None:
    init_library_db()
    with get_db_conn() as conn:
        r = conn.execute(
            "SELECT * FROM library_sources WHERE id = ?", (source_id,)
        ).fetchone()
    return _row(r) if r else None


def list_sources(
    library: str | None = None,
    type: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    """List catalog rows, newest first. `tag` filters on the JSON tags array."""
    init_library_db()
    clauses: list[str] = []
    params: list[Any] = []
    if library:
        clauses.append("library = ?")
        params.append(library)
    if type:
        clauses.append("type = ?")
        params.append(type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM library_sources {where} ORDER BY added_at DESC, id DESC",
            params,
        ).fetchall()
    sources = [_row(r) for r in rows]
    if tag:
        sources = [s for s in sources if tag in s["tags"]]
    return sources


def delete_source(source_id: str) -> bool:
    with get_db_conn() as conn:
        cur = conn.execute("DELETE FROM library_sources WHERE id = ?", (source_id,))
        return cur.rowcount > 0


def list_libraries() -> list[dict[str, Any]]:
    init_library_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            """
            SELECT library,
                   COUNT(*) AS source_count,
                   COALESCE(SUM(chunk_count), 0) AS chunk_count
            FROM library_sources
            GROUP BY library
            ORDER BY library
            """
        ).fetchall()
    return [
        {
            "name": r["library"],
            "source_count": r["source_count"],
            "chunk_count": r["chunk_count"],
        }
        for r in rows
    ]


def chunk_docs_for(source: dict[str, Any]) -> list[dict[str, Any]]:
    """The stored chunks for a source (from the shared `documents` table), ordered
    by chunk index. Chunk counts are small, so a collection scan + filter is fine."""
    docs, _ = list_documents(source["library"], limit=100_000, offset=0)
    chunks = [
        {"index": int(d["metadata"].get("chunk_index", 0)), "text": d["text"]}
        for d in docs
        if d["metadata"].get("source_id") == source["id"]
    ]
    chunks.sort(key=lambda c: c["index"])
    return chunks
