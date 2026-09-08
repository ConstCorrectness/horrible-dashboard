"""Where a GGUF came from.

The loop — recipe, run, checkpoint, GGUF, eval sweep, tracked metrics — was fully
buildable and completely unrecorded. A file called
`my-project-checkpoint-1200-f16.gguf` in the llama.cpp catalog told you a project id
and nothing else: not which base model it was fine-tuned from, not which recipe
produced it, not which run's metrics belong to it. "Score my fine-tune against its
base" needs that last one, and it was something you had to remember by hand.

**The GGUF path is the join key**, so this is a provenance row and not a workflow
engine. Two facts make that work: `RunTarget.model_path` already names a file, and
`convert._output_path` already writes a deterministic name. Everything downstream
already keys off the path; this table just says what the path *means*.

**One writer.** `convert.py` records a row at the one moment every field is known —
it has the project, the checkpoint, the resolved base model and the output type in
hand. Nothing else writes here, so there is no coordination problem and no
half-populated row.

**Several readers.** `GET /api/evals/targets` labels a target with its base model
(and offers "compare against base" because of it), and the llama.cpp catalog can say
what a managed file is.

Lives in `app.db` beside the eval tables, deliberately: "which sweeps scored a
fine-tune of Llama-3.2-3B" is then a join in the `database` console rather than a
feature request. Note the `case_hash` precedent — `CREATE TABLE IF NOT EXISTS` does
nothing to a table that already exists, so a later column needs `_ensure_column`,
which is why that helper is here from the start.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir

logger = logging.getLogger(__name__)

_initialized: set[str] = set()


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    path = str(ensure_app_db_dir())
    if path not in _initialized:
        # Marked before the call, not after: `init_db` opens a connection through
        # this same helper and would otherwise recurse forever. The evals store
        # keys its flag by path for the same reason — `HORRIBLE_DATA_DIR` is
        # env-driven, so a test pointing at a fresh tmp dir must not inherit a
        # `True` from the previous one and query tables that were never created.
        _initialized.add(path)
        init_db()
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


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    """Add a column if it is not there yet. SQLite has no `ADD COLUMN IF NOT EXISTS`."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_lineage (
                gguf_path TEXT PRIMARY KEY,
                project_id TEXT NOT NULL DEFAULT '',
                checkpoint TEXT NOT NULL DEFAULT '',
                base_model TEXT NOT NULL DEFAULT '',
                out_type TEXT NOT NULL DEFAULT '',
                is_adapter INTEGER NOT NULL DEFAULT 0,
                recipe_json TEXT NOT NULL DEFAULT '{}',
                training_run_id TEXT NOT NULL DEFAULT '',
                localtrack_run_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lineage_project ON model_lineage(project_id)"
        )


def record(
    gguf_path: str,
    *,
    project_id: str,
    checkpoint: str,
    base_model: str = "",
    out_type: str = "",
    is_adapter: bool = False,
    recipe: dict[str, Any] | None = None,
    training_run_id: str = "",
    localtrack_run_id: str = "",
) -> None:
    """Record where one GGUF came from. Never raises.

    A conversion that succeeded must not be reported as failed because a
    bookkeeping row could not be written — the file is on disk and servable either
    way. Same posture as the evals sweep's localtrack mirroring.

    Upserts on the path: converting the same checkpoint twice at the same output
    type overwrites the same file, so it must overwrite the same row rather than
    conflict.
    """
    try:
        with _conn() as conn:
            conn.execute(
                """
                INSERT INTO model_lineage
                    (gguf_path, project_id, checkpoint, base_model, out_type,
                     is_adapter, recipe_json, training_run_id, localtrack_run_id,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gguf_path) DO UPDATE SET
                    project_id = excluded.project_id,
                    checkpoint = excluded.checkpoint,
                    base_model = excluded.base_model,
                    out_type = excluded.out_type,
                    is_adapter = excluded.is_adapter,
                    recipe_json = excluded.recipe_json,
                    training_run_id = excluded.training_run_id,
                    localtrack_run_id = excluded.localtrack_run_id,
                    created_at = excluded.created_at
                """,
                (
                    gguf_path,
                    project_id,
                    checkpoint,
                    base_model,
                    out_type,
                    1 if is_adapter else 0,
                    json.dumps(recipe or {}),
                    training_run_id,
                    localtrack_run_id,
                    time.time(),
                ),
            )
    except Exception:  # noqa: BLE001 — bookkeeping must never fail a conversion
        logger.debug(
            "training: could not record lineage for %s", gguf_path, exc_info=True
        )


def _row(r: sqlite3.Row) -> dict[str, Any]:
    try:
        recipe = json.loads(r["recipe_json"] or "{}")
    except (TypeError, ValueError):
        recipe = {}
    return {
        "ggufPath": r["gguf_path"],
        "projectId": r["project_id"],
        "checkpoint": r["checkpoint"],
        "baseModel": r["base_model"],
        "outType": r["out_type"],
        "isAdapter": bool(r["is_adapter"]),
        "recipe": recipe,
        "trainingRunId": r["training_run_id"],
        "localtrackRunId": r["localtrack_run_id"],
        "createdAt": r["created_at"],
    }


def get(gguf_path: str) -> dict[str, Any] | None:
    """One file's provenance, or None when nothing recorded it.

    None is a normal answer, not an error: every GGUF a user downloaded rather than
    trained has no lineage, and a caller must render that as "no provenance" rather
    than inventing one.
    """
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT * FROM model_lineage WHERE gguf_path = ?", (gguf_path,)
            ).fetchone()
        return _row(row) if row else None
    except Exception:  # noqa: BLE001
        logger.debug("training: lineage lookup failed for %s", gguf_path, exc_info=True)
        return None


def by_path() -> dict[str, dict[str, Any]]:
    """Every recorded file, keyed by path — for labelling a whole catalog at once.

    One query rather than one per model: the evals target picker lists every GGUF on
    the machine, and asking the database per row would be a query per file on every
    page load.
    """
    try:
        with _conn() as conn:
            rows = conn.execute("SELECT * FROM model_lineage").fetchall()
        return {r["gguf_path"]: _row(r) for r in rows}
    except Exception:  # noqa: BLE001
        logger.debug("training: lineage listing failed", exc_info=True)
        return {}


def inventory() -> list[dict[str, Any]]:
    """Every model this node made, with where it came from and how it scored.

    The join this module's docstring proposed and nothing performed. Five disjoint
    views of "the models I have" existed — the llama.cpp GGUF catalog, Hugging Face
    downloads, chat-provider model *names*, per-project checkpoints, and this table —
    and the answer to "which fine-tune is this, and did it beat its base?" was to
    write the join by hand in the database console.

    One row per lineage row, not per file on disk: the catalog lists every GGUF the
    node can serve, including ones Ollama and LM Studio own, and those have no
    provenance to report. A model *made here* is what this answers about.

    Every leg is optional and degrades to empty rather than failing. A run whose
    metrics were never mirrored, an eval that was never run, a file since deleted —
    each is a normal state, and a inventory that 500s because one of them is missing
    would be useless exactly when it is most needed. The `present` flag is the one
    fact worth reporting loudly: a row whose file is gone is a fine-tune you can read
    the history of and cannot serve.
    """
    rows = list(by_path().values())
    if not rows:
        return []

    # `_row` already renames every column to camelCase for the browser; reading the
    # SQL spellings here returns None for all of them and joins nothing, silently.
    scores = _scores_by_path()
    metrics = _metrics_by_run()
    for row in rows:
        path = str(row.get("ggufPath") or "")
        present = bool(path) and Path(path).exists()
        row["present"] = present
        row["sizeBytes"] = Path(path).stat().st_size if present else 0
        row["evals"] = scores.get(path, [])
        row["metrics"] = metrics.get(str(row.get("localtrackRunId") or ""), {})
    rows.sort(key=lambda r: float(r.get("createdAt") or 0), reverse=True)
    return rows


def _scores_by_path() -> dict[str, list[dict[str, Any]]]:
    """Finished eval runs, grouped by the GGUF they scored.

    Keyed on `model_path` rather than the model *name*: a name is an alias a server
    answers to and may mean a different file tomorrow, which is the whole reason the
    lineage table is keyed on the path. Runs from before that column existed carry an
    empty path and are simply not attributed — better than attaching a score to a
    file it may not have measured.
    """
    try:
        from backend.modules.evals import store as evals_store

        runs = evals_store.list_runs(limit=500)
    except Exception:  # noqa: BLE001 - a missing leg is a normal state
        logger.debug("lineage: eval scores unavailable", exc_info=True)
        return {}

    out: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        path = getattr(run, "model_path", "")
        if not path or run.status != "done":
            continue
        out.setdefault(path, []).append(
            {
                "runId": run.id,
                "suiteId": run.suite_id,
                "passed": run.passed,
                "total": run.total,
                "completed": run.completed,
                "finishedAt": run.finished_at,
            }
        )
    return out


def _metrics_by_run() -> dict[str, dict[str, float]]:
    """Each lineage row's localtrack run summary, keyed by run id."""
    try:
        from backend.modules.localtrack import store as lt_store

        wanted = {
            str(r.get("localtrackRunId") or "")
            for r in by_path().values()
            if r.get("localtrackRunId")
        }
        out: dict[str, dict[str, float]] = {}
        for run_id in wanted:
            run = lt_store.get_run(run_id)
            if run is not None:
                out[run_id] = dict(run.summary or {})
        return out
    except Exception:  # noqa: BLE001
        logger.debug("lineage: localtrack summaries unavailable", exc_info=True)
        return {}


def forget(gguf_path: str) -> None:
    """Drop a row — for when the file it describes is deleted."""
    try:
        with _conn() as conn:
            conn.execute("DELETE FROM model_lineage WHERE gguf_path = ?", (gguf_path,))
    except Exception:  # noqa: BLE001
        logger.debug("training: lineage delete failed for %s", gguf_path, exc_info=True)
