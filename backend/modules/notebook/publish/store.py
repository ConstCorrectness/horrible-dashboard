"""Where each notebook has been published: `notebook_publications` in `app.db`.

In `app.db` rather than in the `.ipynb` metadata, for two reasons: publishing must never
rewrite the user's file, and a publish record inside a notebook would itself get
published — a gist carrying its own gist id, forever.

Keyed by the notebook's path **relative to the notebook root** plus the target, so a
second publish to the same place updates it. The cost, stated plainly: renaming or
moving a notebook forgets where it was published, and the next publish makes a new
copy rather than updating the old one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager

from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.notebook.models import PublicationModel

_DDL = """
CREATE TABLE IF NOT EXISTS notebook_publications (
    path TEXT NOT NULL,
    target TEXT NOT NULL,
    remote_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    extra_url TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'secret',
    published_at REAL NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (path, target)
)
"""


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(ensure_app_db_dir()))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_DDL)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row) -> PublicationModel:
    try:
        detail = json.loads(row["detail"] or "{}")
    except ValueError:
        detail = {}
    return PublicationModel(
        path=row["path"],
        target=row["target"],
        remote_id=row["remote_id"],
        url=row["url"],
        extra_url=row["extra_url"],
        visibility=row["visibility"],
        published_at=row["published_at"],
        detail=detail,
    )


def get(path: str, target: str) -> PublicationModel | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM notebook_publications WHERE path = ? AND target = ?",
            (path, target),
        ).fetchone()
    return _row(row) if row else None


def list_for(path: str) -> list[PublicationModel]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notebook_publications WHERE path = ? ORDER BY published_at DESC",
            (path,),
        ).fetchall()
    return [_row(r) for r in rows]


def upsert(record: PublicationModel) -> PublicationModel:
    record = record.model_copy(
        update={"published_at": record.published_at or time.time()}
    )
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO notebook_publications
                (path, target, remote_id, url, extra_url, visibility, published_at, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path, target) DO UPDATE SET
                remote_id = excluded.remote_id,
                url = excluded.url,
                extra_url = excluded.extra_url,
                visibility = excluded.visibility,
                published_at = excluded.published_at,
                detail = excluded.detail
            """,
            (
                record.path,
                record.target,
                record.remote_id,
                record.url,
                record.extra_url,
                record.visibility,
                record.published_at,
                json.dumps(record.detail),
            ),
        )
    return record


def delete(path: str, target: str) -> None:
    with _conn() as conn:
        conn.execute(
            "DELETE FROM notebook_publications WHERE path = ? AND target = ?",
            (path, target),
        )
