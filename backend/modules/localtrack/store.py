"""Persistent SQLite store for LocalTrack projects, runs, metrics, and artifacts."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from backend import paths
from backend.modules.localtrack import stream
from backend.modules.localtrack.downsampling import ema_smooth, lttb
from backend.modules.localtrack.models import (
    MetricLogItem,
    MetricSeriesResponse,
    ProjectModel,
    RunArtifactModel,
    RunModel,
    RunStatus,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    """Path to the LocalTrack SQLite database under data_dir."""
    data_dir = paths.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "localtrack.db"


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Provide a thread-safe connection to the LocalTrack database with WAL mode."""
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize LocalTrack database tables and indexes."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lt_projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lt_runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                config_json TEXT DEFAULT '{}',
                system_info_json TEXT DEFAULT '{}',
                summary_json TEXT DEFAULT '{}',
                tags_json TEXT DEFAULT '[]',
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration_seconds REAL DEFAULT 0.0,
                FOREIGN KEY (project_id) REFERENCES lt_projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS lt_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                key TEXT NOT NULL,
                step INTEGER NOT NULL,
                epoch REAL,
                value REAL NOT NULL,
                timestamp REAL NOT NULL,
                FOREIGN KEY (run_id) REFERENCES lt_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS lt_artifacts (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                size_bytes INTEGER DEFAULT 0,
                content_type TEXT DEFAULT 'application/octet-stream',
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES lt_runs(id) ON DELETE CASCADE
            );

            -- The pane's panel arrangement, per project.
            --
            -- It lived in `localStorage` under `localtrack_panels_<projectId>`,
            -- which is per-browser-origin: the arrangement you built in the
            -- browser layout was invisible in the desktop shell and vice versa,
            -- and clearing site data silently reset it. Stored as an opaque blob
            -- the backend never interprets — the `/api/flows` precedent, and the
            -- reason a new panel type needs no migration here.
            CREATE TABLE IF NOT EXISTS lt_layouts (
                project_id TEXT PRIMARY KEY,
                panels_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES lt_projects(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_lt_runs_project ON lt_runs(project_id);
            CREATE INDEX IF NOT EXISTS idx_lt_metrics_lookup ON lt_metrics(run_id, key, step);
            CREATE INDEX IF NOT EXISTS idx_lt_artifacts_run ON lt_artifacts(run_id);
            """
        )
        # Ensure default project exists
        existing = conn.execute(
            "SELECT id FROM lt_projects WHERE id = 'default'"
        ).fetchone()
        if not existing:
            now = _utc_now_iso()
            conn.execute(
                "INSERT INTO lt_projects (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("default", "Default Project", "Default experiment tracking project", now, now),
            )


# --- Projects CRUD ---


def list_projects() -> list[ProjectModel]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.description, p.created_at, p.updated_at,
                   COUNT(r.id) as run_count,
                   MAX(r.start_time) as last_run_at
            FROM lt_projects p
            LEFT JOIN lt_runs r ON p.id = r.project_id
            GROUP BY p.id
            ORDER BY p.updated_at DESC
            """
        ).fetchall()
        return [
            ProjectModel(
                id=r["id"],
                name=r["name"],
                description=r["description"] or "",
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                run_count=r["run_count"] or 0,
                last_run_at=r["last_run_at"],
            )
            for r in rows
        ]


def create_project(project_id: str | None, name: str, description: str = "") -> ProjectModel:
    init_db()
    pid = project_id or name.lower().replace(" ", "-").replace("/", "-")
    pid = "".join(c for c in pid if c.isalnum() or c in "-_") or str(uuid.uuid4())[:8]
    now = _utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO lt_projects (id, name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pid, name, description, now, now),
        )
    return ProjectModel(
        id=pid,
        name=name,
        description=description,
        created_at=now,
        updated_at=now,
        run_count=0,
    )


def get_project(project_id: str) -> ProjectModel | None:
    init_db()
    with get_conn() as conn:
        r = conn.execute(
            """
            SELECT p.id, p.name, p.description, p.created_at, p.updated_at,
                   COUNT(r.id) as run_count,
                   MAX(r.start_time) as last_run_at
            FROM lt_projects p
            LEFT JOIN lt_runs r ON p.id = r.project_id
            WHERE p.id = ?
            GROUP BY p.id
            """,
            (project_id,),
        ).fetchone()
        if not r:
            return None
        return ProjectModel(
            id=r["id"],
            name=r["name"],
            description=r["description"] or "",
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            run_count=r["run_count"] or 0,
            last_run_at=r["last_run_at"],
        )


def delete_project(project_id: str) -> bool:
    init_db()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM lt_projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0


# --- Runs CRUD ---


def list_runs(project_id: str | None = None) -> list[RunModel]:
    init_db()
    with get_conn() as conn:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM lt_runs WHERE project_id = ? ORDER BY start_time DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM lt_runs ORDER BY start_time DESC").fetchall()
        return [_row_to_run(r) for r in rows]


def get_run(run_id: str) -> RunModel | None:
    init_db()
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM lt_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(r) if r else None


def create_run(
    run_id: str | None,
    project_id: str,
    name: str | None,
    config: dict[str, Any] | None = None,
    system_info: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> RunModel:
    init_db()
    # Ensure project exists
    if not get_project(project_id):
        create_project(project_id, project_id.title())

    rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
    rname = name or rid
    now = _utc_now_iso()
    cfg_json = json.dumps(config or {})
    sys_json = json.dumps(system_info or {})
    tags_json = json.dumps(tags or [])

    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO lt_runs
            (id, project_id, name, status, config_json, system_info_json, summary_json, tags_json, start_time, duration_seconds)
            VALUES (?, ?, ?, 'running', ?, ?, '{}', ?, ?, 0.0)
            """,
            (rid, project_id, rname, cfg_json, sys_json, tags_json, now),
        )
        conn.execute(
            "UPDATE lt_projects SET updated_at = ? WHERE id = ?",
            (now, project_id),
        )

    stream.publish("run_created", {"runId": rid, "projectId": project_id, "name": rname})

    return RunModel(
        id=rid,
        project_id=project_id,
        name=rname,
        status="running",
        config=config or {},
        system_info=system_info or {},
        summary={},
        tags=tags or [],
        start_time=now,
    )


def update_run(
    run_id: str,
    name: str | None = None,
    status: RunStatus | None = None,
    config: dict[str, Any] | None = None,
    summary: dict[str, float | int] | None = None,
    tags: list[str] | None = None,
    end_time: str | None = None,
    duration_seconds: float | None = None,
) -> RunModel | None:
    init_db()
    run = get_run(run_id)
    if not run:
        return None

    fields: list[str] = []
    params: list[Any] = []

    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if config is not None:
        fields.append("config_json = ?")
        params.append(json.dumps(config))
    if summary is not None:
        # Merge summary
        new_summary = dict(run.summary)
        new_summary.update(summary)
        fields.append("summary_json = ?")
        params.append(json.dumps(new_summary))
    if tags is not None:
        fields.append("tags_json = ?")
        params.append(json.dumps(tags))
    if end_time is not None:
        fields.append("end_time = ?")
        params.append(end_time)
    if duration_seconds is not None:
        fields.append("duration_seconds = ?")
        params.append(duration_seconds)

    if fields:
        params.append(run_id)
        with get_conn() as conn:
            conn.execute(
                f"UPDATE lt_runs SET {', '.join(fields)} WHERE id = ?",
                tuple(params),
            )

    updated = get_run(run_id)
    if updated is not None:
        stream.publish(
            "run_updated",
            {
                "runId": run_id,
                "projectId": updated.project_id,
                "status": updated.status,
            },
        )
    return updated


def delete_run(run_id: str) -> bool:
    init_db()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM lt_runs WHERE id = ?", (run_id,))
        deleted = cur.rowcount > 0
    if deleted:
        stream.publish("run_deleted", {"runId": run_id})
    return deleted


def _row_to_run(r: sqlite3.Row) -> RunModel:
    return RunModel(
        id=r["id"],
        project_id=r["project_id"],
        name=r["name"],
        status=r["status"],
        config=json.loads(r["config_json"] or "{}"),
        system_info=json.loads(r["system_info_json"] or "{}"),
        summary=json.loads(r["summary_json"] or "{}"),
        tags=json.loads(r["tags_json"] or "[]"),
        start_time=r["start_time"],
        end_time=r["end_time"],
        duration_seconds=r["duration_seconds"] or 0.0,
    )


# --- Metrics Batch Ingestion & Querying ---


def ingest_metrics(items: list[MetricLogItem]) -> int:
    """High-throughput batch insertion of metric points."""
    init_db()
    if not items:
        return 0

    now_ts = time.time()
    rows_to_insert: list[tuple[str, str, int, float | None, float, float]] = []
    latest_summaries: dict[str, dict[str, float | int]] = {}

    for item in items:
        ts = item.timestamp if item.timestamp is not None else now_ts
        if item.run_id not in latest_summaries:
            latest_summaries[item.run_id] = {}
        for key, val in item.metrics.items():
            try:
                num_val = float(val)
            except (ValueError, TypeError):
                continue
            rows_to_insert.append((item.run_id, key, item.step, item.epoch, num_val, ts))
            latest_summaries[item.run_id][key] = num_val

    if not rows_to_insert:
        return 0

    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO lt_metrics (run_id, key, step, epoch, value, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        # Update run summaries
        for run_id, summary_updates in latest_summaries.items():
            r = conn.execute("SELECT summary_json FROM lt_runs WHERE id = ?", (run_id,)).fetchone()
            if r:
                curr_summary = json.loads(r["summary_json"] or "{}")
                curr_summary.update(summary_updates)
                conn.execute(
                    "UPDATE lt_runs SET summary_json = ? WHERE id = ?",
                    (json.dumps(curr_summary), run_id),
                )

    # Announce AFTER the commit, never before: a pane that refetches on the event
    # would otherwise race the writer and read the pre-write state, which looks
    # exactly like a dropped point.
    for run_id, summary_updates in latest_summaries.items():
        stream.publish_metrics(run_id, list(summary_updates))

    return len(rows_to_insert)


def get_metric_keys(run_ids: list[str] | None = None, project_id: str | None = None) -> list[str]:
    """Discover all distinct metric keys logged for the given runs or project."""
    init_db()
    with get_conn() as conn:
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            rows = conn.execute(
                f"SELECT DISTINCT key FROM lt_metrics WHERE run_id IN ({placeholders}) ORDER BY key",
                run_ids,
            ).fetchall()
        elif project_id:
            rows = conn.execute(
                """
                SELECT DISTINCT m.key
                FROM lt_metrics m
                JOIN lt_runs r ON m.run_id = r.id
                WHERE r.project_id = ?
                ORDER BY m.key
                """,
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT DISTINCT key FROM lt_metrics ORDER BY key").fetchall()
        return [r["key"] for r in rows]


def query_metrics(
    run_ids: list[str],
    keys: list[str],
    max_points: int = 500,
    smoothing: float = 0.0,
    min_step: int | None = None,
    max_step: int | None = None,
) -> list[MetricSeriesResponse]:
    """Query time-series metrics across multiple runs with server-side downsampling and smoothing."""
    init_db()
    if not run_ids or not keys:
        return []

    results: list[MetricSeriesResponse] = []

    with get_conn() as conn:
        for run_id in run_ids:
            for key in keys:
                query = [
                    "SELECT step, epoch, value FROM lt_metrics WHERE run_id = ? AND key = ?"
                ]
                params: list[Any] = [run_id, key]
                if min_step is not None:
                    query.append("AND step >= ?")
                    params.append(min_step)
                if max_step is not None:
                    query.append("AND step <= ?")
                    params.append(max_step)
                query.append("ORDER BY step ASC")

                rows = conn.execute(" ".join(query), tuple(params)).fetchall()
                raw_count = len(rows)
                if raw_count == 0:
                    continue

                pts = [(float(r["step"]), float(r["value"])) for r in rows]
                if max_points > 0 and raw_count > max_points:
                    downsampled_pts = lttb(pts, max_points)
                else:
                    downsampled_pts = pts

                steps = [int(p[0]) for p in downsampled_pts]
                raw_values = [p[1] for p in downsampled_pts]

                if smoothing > 0.0:
                    values = ema_smooth(raw_values, smoothing)
                else:
                    values = raw_values

                results.append(
                    MetricSeriesResponse(
                        run_id=run_id,
                        key=key,
                        steps=steps,
                        values=values,
                        epochs=[],
                        raw_point_count=raw_count,
                    )
                )

    return results


# --- Artifacts Management ---


def get_artifacts_dir(run_id: str) -> Path:
    data_dir = paths.data_dir()
    art_dir = data_dir / "localtrack_artifacts" / run_id
    art_dir.mkdir(parents=True, exist_ok=True)
    return art_dir


def save_artifact(
    run_id: str,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> RunArtifactModel:
    init_db()
    run_dir = get_artifacts_dir(run_id)
    # Sanitize filename
    safe_name = Path(filename).name
    file_path = run_dir / safe_name
    file_path.write_bytes(content)

    art_id = f"art-{uuid.uuid4().hex[:8]}"
    now = _utc_now_iso()
    size = len(content)

    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO lt_artifacts (id, run_id, filename, file_path, size_bytes, content_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (art_id, run_id, safe_name, str(file_path), size, content_type, now),
        )

    return RunArtifactModel(
        id=art_id,
        run_id=run_id,
        filename=safe_name,
        file_path=str(file_path),
        size_bytes=size,
        content_type=content_type,
        created_at=now,
    )


def list_artifacts(run_id: str) -> list[RunArtifactModel]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM lt_artifacts WHERE run_id = ? ORDER BY created_at ASC",
            (run_id,),
        ).fetchall()
        return [
            RunArtifactModel(
                id=r["id"],
                run_id=r["run_id"],
                filename=r["filename"],
                file_path=r["file_path"],
                size_bytes=r["size_bytes"] or 0,
                content_type=r["content_type"] or "application/octet-stream",
                created_at=r["created_at"],
            )
            for r in rows
        ]


def get_artifact(artifact_id: str) -> RunArtifactModel | None:
    init_db()
    with get_conn() as conn:
        r = conn.execute(
            "SELECT * FROM lt_artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        if not r:
            return None
        return RunArtifactModel(
            id=r["id"],
            run_id=r["run_id"],
            filename=r["filename"],
            file_path=r["file_path"],
            size_bytes=r["size_bytes"] or 0,
            content_type=r["content_type"] or "application/octet-stream",
            created_at=r["created_at"],
        )


# --- Panel layout (opaque blob, per project) ---


def get_layout(project_id: str) -> list[dict[str, Any]] | None:
    """The saved panel arrangement, or None when the project has never saved one.

    None and `[]` are different answers and the caller depends on it: None means
    "use the defaults", while an empty list means "the user removed every panel".
    Collapsing them would make a deliberately cleared workspace spring back to the
    four default charts on every reload.
    """
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT panels_json FROM lt_layouts WHERE project_id = ?", (project_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        parsed = json.loads(row["panels_json"])
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def save_layout(project_id: str, panels: list[dict[str, Any]]) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO lt_layouts (project_id, panels_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                panels_json = excluded.panels_json,
                updated_at = excluded.updated_at
            """,
            (project_id, json.dumps(panels), _utc_now_iso()),
        )
