"""The artifact store — the node's on-disk blob store.

Blobs live at ``$HORRIBLE_DATA_DIR/artifacts/<sha256[:2]>/<sha256>`` and rows in an
``artifacts`` table in `app.db`. The path is derived *only* from the content hash,
so a row can never address a file outside the store — path traversal is impossible
by construction, and identical bytes stored twice share one blob (rows stay
independent; the blob is unlinked only when its last referent is deleted).

Kinds today: ``pdf`` (stored papers/documents), ``page`` (self-contained captured
HTML), ``report`` (deep-research output markdown). The row carries mime/filename;
the on-disk file has no extension.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from backend.modules.database.app_db import ensure_app_db_dir, get_data_dir

from contextlib import contextmanager
import sqlite3
from typing import Generator

ARTIFACT_KINDS: frozenset[str] = frozenset({"pdf", "page", "report"})


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


def init_artifacts_db() -> None:
    """Create the artifacts table (idempotent)."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                kind TEXT NOT NULL,
                mime TEXT NOT NULL,
                filename TEXT NOT NULL,
                size INTEGER NOT NULL,
                origin_url TEXT,
                meta TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_sha ON artifacts(sha256)"
        )


def artifacts_dir() -> Path:
    return get_data_dir() / "artifacts"


def _blob_path(sha256: str) -> Path:
    return artifacts_dir() / sha256[:2] / sha256


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "sha256": r["sha256"],
        "kind": r["kind"],
        "mime": r["mime"],
        "filename": r["filename"],
        "size": r["size"],
        "origin_url": r["origin_url"],
        "meta": json.loads(r["meta"] or "{}"),
        "created_at": str(r["created_at"]),
    }


def store_bytes(
    data: bytes,
    *,
    kind: str,
    mime: str,
    filename: str,
    origin_url: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store a blob and return its artifact row. Content-addressed: identical bytes
    reuse the existing file on disk."""
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"unknown artifact kind: {kind!r}")
    init_artifacts_db()
    sha = hashlib.sha256(data).hexdigest()
    path = _blob_path(sha)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    artifact_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO artifacts (id, sha256, kind, mime, filename, size, origin_url, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                sha,
                kind,
                mime,
                filename,
                len(data),
                origin_url,
                json.dumps(meta or {}),
            ),
        )
    artifact = get_artifact(artifact_id)
    assert artifact is not None  # just inserted
    return artifact


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    init_artifacts_db()
    with get_db_conn() as conn:
        r = conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
    return _row(r) if r else None


def artifact_path(artifact_id: str) -> Path | None:
    """Absolute path of an artifact's blob, or None if the row doesn't exist.
    Derived from the stored hash only — never from user input."""
    artifact = get_artifact(artifact_id)
    if artifact is None:
        return None
    return _blob_path(artifact["sha256"])


def list_artifacts(kind: str | None = None) -> list[dict[str, Any]]:
    init_artifacts_db()
    with get_db_conn() as conn:
        if kind:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE kind = ? ORDER BY created_at DESC, id DESC",
                (kind,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM artifacts ORDER BY created_at DESC, id DESC"
            ).fetchall()
    return [_row(r) for r in rows]


def delete_artifact(artifact_id: str) -> bool:
    """Delete the row; unlink the blob only when no other row references its hash."""
    artifact = get_artifact(artifact_id)
    if artifact is None:
        return False
    with get_db_conn() as conn:
        conn.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
        remaining = conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE sha256 = ?", (artifact["sha256"],)
        ).fetchone()[0]
    if remaining == 0:
        _blob_path(artifact["sha256"]).unlink(missing_ok=True)
    return True
