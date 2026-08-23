"""The `llamacpp_traces` catalog — one row per stored trace.

The traces themselves are files: a `manifest.json` plus one append-only
`tensors.bin` per directory under `$HORRIBLE_DATA_DIR/llamacpp/traces/`. That is
the right home for the *blobs* — a gigabyte of fp16 activations does not belong in
SQLite — but it makes the set of traces answerable only by walking a directory and
parsing every manifest, which is to say answerable only by this module's own code.

So the **catalog** lives in `app.db` alongside `library_sources` and
`karaoke_songs`: the same split those two already use, index in the database and
payload on disk. What it buys is joins. `agent_turns` is in the same file, so
"which traces were taken while the agent was doing X" is one query in the
`database` console rather than a script; and `derived_from` makes a fork chain a
recursive CTE rather than a manual walk of `manifest.json` files.

The disk stays authoritative. A row is a *description* of a directory, never the
thing itself: `sync()` reconciles both ways (rows whose directory is gone are
deleted, directories with no row are inserted), and is called at startup because a
node that traced before this table existed would otherwise show an empty catalog
and read that as "no traces". Nothing here is load-bearing for the lens — deleting
`app.db` costs you the query surface and not a single activation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.llamacpp import traces

logger = logging.getLogger(__name__)

# Database files this process has already run the DDL against, keyed by path for
# the reason `karaoke.store` documents: `HORRIBLE_DATA_DIR` is env-driven, and a
# test pointing it at a fresh tmp dir must not inherit a `True` from the last one.
_initialized: set[str] = set()


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    path = str(ensure_app_db_dir())
    if path not in _initialized:
        # Marked *before* the call: `init_trace_catalog_db` opens a connection
        # through this same helper, so marking after would recurse forever.
        _initialized.add(path)
        init_trace_catalog_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_trace_catalog_db() -> None:
    """Create the catalog table (idempotent)."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llamacpp_traces (
                id TEXT PRIMARY KEY,
                model_name TEXT NOT NULL DEFAULT '',
                model_path TEXT NOT NULL DEFAULT '',
                model_sha TEXT NOT NULL DEFAULT '',
                llama_build TEXT NOT NULL DEFAULT '',
                architecture TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL DEFAULT '',
                token_source TEXT NOT NULL DEFAULT '',
                fidelity TEXT NOT NULL DEFAULT '',
                attention INTEGER NOT NULL DEFAULT 0,
                capture_json TEXT NOT NULL DEFAULT '[]',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                max_tokens INTEGER NOT NULL DEFAULT 0,
                record_count INTEGER NOT NULL DEFAULT 0,
                disk_bytes INTEGER NOT NULL DEFAULT 0,
                derived_from TEXT NOT NULL DEFAULT '',
                edits_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        # The two questions asked of this table that are not "by id": every trace
        # of one model (the lens picker, and `matches_run`'s precondition), and
        # the children of one trace (a fork chain).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llamacpp_traces_sha "
            "ON llamacpp_traces(model_sha)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llamacpp_traces_parent "
            "ON llamacpp_traces(derived_from)"
        )


_COLUMNS = (
    "id",
    "model_name",
    "model_path",
    "model_sha",
    "llama_build",
    "architecture",
    "prompt",
    "token_source",
    "fidelity",
    "attention",
    "capture_json",
    "prompt_tokens",
    "max_tokens",
    "record_count",
    "disk_bytes",
    "derived_from",
    "edits_json",
    "created_at",
)


def _row_for(trace: traces.Trace) -> dict[str, Any]:
    m = trace.manifest
    return {
        "id": trace.trace_id,
        "model_name": str(m.get("modelName") or ""),
        "model_path": str(m.get("modelPath") or ""),
        "model_sha": str(m.get("modelSha") or ""),
        "llama_build": str(m.get("llamaBuild") or ""),
        "architecture": str(m.get("architecture") or ""),
        "prompt": str(m.get("prompt") or ""),
        "token_source": str(m.get("tokenSource") or ""),
        "fidelity": str(m.get("fidelity") or ""),
        "attention": 1 if m.get("attention") else 0,
        "capture_json": json.dumps(list(m.get("capture") or [])),
        "prompt_tokens": int(m.get("promptTokens") or 0),
        "max_tokens": int(m.get("maxTokens") or 0),
        "record_count": int(m.get("recordCount") or 0),
        "disk_bytes": trace.bytes_on_disk(),
        "derived_from": str(m.get("derivedFrom") or ""),
        "edits_json": json.dumps(list(m.get("edits") or [])),
        "created_at": float(m.get("createdAt") or 0.0),
    }


def record(trace: traces.Trace) -> None:
    """Upsert one trace's row. Never raises into a caller that was tracing.

    A catalog write failing must not turn a completed trace into a failed one —
    the activations are already on disk and are the thing that was expensive.
    """
    row = _row_for(trace)
    placeholders = ", ".join("?" for _ in _COLUMNS)
    columns = ", ".join(_COLUMNS)
    try:
        with get_db_conn() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO llamacpp_traces ({columns}) "
                f"VALUES ({placeholders})",
                [row[c] for c in _COLUMNS],
            )
    except sqlite3.Error as exc:
        logger.warning("llamacpp: could not catalog trace %s (%s)", trace.trace_id, exc)


def forget(trace_id: str) -> None:
    """Drop one trace's row. Called when its directory goes away."""
    try:
        with get_db_conn() as conn:
            conn.execute("DELETE FROM llamacpp_traces WHERE id = ?", (trace_id,))
    except sqlite3.Error as exc:
        logger.warning("llamacpp: could not uncatalog trace %s (%s)", trace_id, exc)


def sync() -> dict[str, int]:
    """Reconcile the catalog with the trace directory, both ways.

    Disk is the authority. A directory with no row is inserted (traces made
    before this table existed, or a data dir copied in from elsewhere); a row
    whose directory is gone is deleted (a manual `rm -rf`, which nothing else
    would ever tell us about).
    """
    on_disk = {t.trace_id: t for t in traces.list_traces()}
    try:
        with get_db_conn() as conn:
            known = {
                str(r["id"]) for r in conn.execute("SELECT id FROM llamacpp_traces")
            }
            stale = known - set(on_disk)
            if stale:
                conn.executemany(
                    "DELETE FROM llamacpp_traces WHERE id = ?",
                    [(trace_id,) for trace_id in stale],
                )
    except sqlite3.Error as exc:
        logger.warning("llamacpp: trace catalog sync failed (%s)", exc)
        return {"added": 0, "removed": 0}
    added = 0
    for trace_id, trace in on_disk.items():
        if trace_id not in known:
            record(trace)
            added += 1
    return {"added": added, "removed": len(stale)}


def rows(
    limit: int = 50, model_sha: str = "", derived_from: str = ""
) -> list[dict[str, Any]]:
    """Catalog rows, newest first. Filters are exact-match and optional."""
    clauses: list[str] = []
    params: list[Any] = []
    if model_sha:
        clauses.append("model_sha = ?")
        params.append(model_sha)
    if derived_from:
        clauses.append("derived_from = ?")
        params.append(derived_from)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(limit, 500)))
    try:
        with get_db_conn() as conn:
            found = conn.execute(
                f"SELECT * FROM llamacpp_traces{where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("llamacpp: trace catalog read failed (%s)", exc)
        return []
    return [_public(r) for r in found]


def _loads(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _public(r: Any) -> dict[str, Any]:
    """One row in the shape the tools and `dash.lens` speak — camelCase, and the
    two JSON columns parsed, so a caller never has to know they were text."""
    return {
        "traceId": r["id"],
        "modelName": r["model_name"],
        "modelPath": r["model_path"],
        "modelSha": r["model_sha"],
        "llamaBuild": r["llama_build"],
        "architecture": r["architecture"],
        "prompt": r["prompt"],
        "tokenSource": r["token_source"],
        "fidelity": r["fidelity"],
        "attention": bool(r["attention"]),
        "capture": _loads(r["capture_json"], []),
        "promptTokens": r["prompt_tokens"],
        "maxTokens": r["max_tokens"],
        "recordCount": r["record_count"],
        "diskBytes": r["disk_bytes"],
        "derivedFrom": r["derived_from"],
        "edits": _loads(r["edits_json"], []),
        "createdAt": r["created_at"],
    }
