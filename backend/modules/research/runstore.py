"""Persistence for deep-research runs: the `research_runs` / `research_steps`
tables in `app.db`.

This is the durable half of the Temporal-inspired runner: every unit of work is
a **step row** whose output and full transcript are persisted *before* the step
is marked done, so a crash mid-run loses at most the step in flight — the
runner's resume pass resets `running` steps to `pending` (keeping the attempt
count) and the run continues where it stopped. Copies the resumable-state
discipline of `google_sync.py` (own tables, per-unit persistence) rather than
generalizing the serial `tasks/queue.py`, whose short-job consumers a
ten-minute research run would starve.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from backend.modules.database.app_db import ensure_app_db_dir

from contextlib import contextmanager
import sqlite3
from typing import Generator

RUN_STATUSES = (
    "pending",
    "planning",
    # Parked between the plan and the subagents, waiting for the user to approve or
    # edit the plan. Deliberately NOT resumable — see `list_resumable_runs`.
    "awaiting_plan",
    "researching",
    "synthesizing",
    "verifying",
    "citing",
    "exporting",
    "done",
    "failed",
    "cancelled",
)
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
STEP_STATUSES = ("pending", "running", "done", "failed", "skipped")
# `critique` closes a research round by naming what's still missing; `verify` checks
# the synthesized claims against their sources before citations rewrites the report.
STEP_KINDS = (
    "plan",
    "subagent",
    "critique",
    "synthesis",
    "verify",
    "citations",
    "export",
)


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


def init_research_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_runs (
                id TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                effort TEXT NOT NULL DEFAULT 'auto',
                library TEXT NOT NULL DEFAULT 'default',
                provider TEXT,
                model TEXT,
                plan TEXT,
                report_artifact_id TEXT,
                report_source_id TEXT,
                error TEXT,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                token_budget INTEGER NOT NULL DEFAULT 200000,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_steps (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                input TEXT NOT NULL DEFAULT '{}',
                output TEXT,
                transcript TEXT,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                started_at TIMESTAMP,
                finished_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_steps_run "
            "ON research_steps(run_id, seq)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_followups (
                id          TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                text        TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                consumed_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_tool_calls (
                id         TEXT PRIMARY KEY,
                run_id     TEXT NOT NULL,
                step_id    TEXT NOT NULL,
                seq        INTEGER NOT NULL,
                name       TEXT NOT NULL,
                args       TEXT,
                ok         INTEGER NOT NULL,
                ms         INTEGER,
                summary    TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_tool_calls_step "
            "ON research_tool_calls(step_id, seq)"
        )

        # Columns added after the tables shipped. `CREATE TABLE IF NOT EXISTS` is a
        # no-op on an existing install, so without this an upgraded node would
        # silently never gain them and every write would fail on an unknown column.
        _ensure_column(conn, "research_steps", "round", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(
            conn, "research_runs", "approval_mode", "TEXT NOT NULL DEFAULT 'auto'"
        )
        _ensure_column(
            conn, "research_runs", "rounds_used", "INTEGER NOT NULL DEFAULT 0"
        )


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    """Add a column if it isn't there yet. SQLite has no `ADD COLUMN IF NOT EXISTS`."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _run_row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "query": r["query"],
        "status": r["status"],
        "effort": r["effort"],
        "library": r["library"],
        "provider": r["provider"],
        "model": r["model"],
        "plan": json.loads(r["plan"]) if r["plan"] else None,
        "report_artifact_id": r["report_artifact_id"],
        "report_source_id": r["report_source_id"],
        "error": r["error"],
        "tokens_used": r["tokens_used"],
        "token_budget": r["token_budget"],
        "cancel_requested": bool(r["cancel_requested"]),
        "approval_mode": r["approval_mode"],
        "rounds_used": r["rounds_used"],
        "created_at": str(r["created_at"]),
        "updated_at": str(r["updated_at"]),
    }


def _step_row(r: Any, *, include_transcript: bool = False) -> dict[str, Any]:
    step = {
        "id": r["id"],
        "run_id": r["run_id"],
        "seq": r["seq"],
        "kind": r["kind"],
        "name": r["name"],
        "round": r["round"],
        "status": r["status"],
        "attempt": r["attempt"],
        "max_attempts": r["max_attempts"],
        "input": json.loads(r["input"] or "{}"),
        "output": json.loads(r["output"]) if r["output"] else None,
        "tokens_used": r["tokens_used"],
        "error": r["error"],
        "started_at": str(r["started_at"]) if r["started_at"] else None,
        "finished_at": str(r["finished_at"]) if r["finished_at"] else None,
    }
    if include_transcript:
        step["transcript"] = json.loads(r["transcript"]) if r["transcript"] else None
    return step


# --- runs -------------------------------------------------------------------


def create_run(
    *,
    query: str,
    effort: str = "auto",
    library: str = "default",
    provider: str | None = None,
    model: str | None = None,
    token_budget: int = 200_000,
    approval_mode: str = "auto",
) -> dict[str, Any]:
    init_research_db()
    run_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO research_runs
                (id, query, effort, library, provider, model, token_budget,
                 approval_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                query,
                effort,
                library,
                provider,
                model,
                token_budget,
                approval_mode,
            ),
        )
    run = get_run(run_id)
    assert run is not None
    return run


def get_run(run_id: str) -> dict[str, Any] | None:
    init_research_db()
    with get_db_conn() as conn:
        r = conn.execute(
            "SELECT * FROM research_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return _run_row(r) if r else None


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    init_research_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM research_runs ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_run_row(r) for r in rows]


def list_resumable_runs() -> list[dict[str, Any]]:
    """Runs that were in flight (or never started) when the process died.

    `awaiting_plan` is excluded even though it isn't terminal: a run parked at the
    approval gate is waiting on a *person*, so re-enqueueing it on every boot would
    spin it through the gate and straight back out forever.
    """
    init_research_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM research_runs "
            "WHERE status NOT IN ('done','failed','cancelled','awaiting_plan') "
            "ORDER BY created_at ASC"
        ).fetchall()
    return [_run_row(r) for r in rows]


def update_run(run_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "plan",
        "report_artifact_id",
        "report_source_id",
        "error",
        "tokens_used",
        "provider",
        "model",
        "approval_mode",
        "rounds_used",
    }
    sets: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"not an updatable run field: {key}")
        sets.append(f"{key} = ?")
        params.append(
            json.dumps(value) if key == "plan" and value is not None else value
        )
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(run_id)
    with get_db_conn() as conn:
        conn.execute(f"UPDATE research_runs SET {', '.join(sets)} WHERE id = ?", params)


def add_run_tokens(run_id: str, tokens: int) -> int:
    """Accumulate token usage; returns the new total."""
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE research_runs SET tokens_used = tokens_used + ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (tokens, run_id),
        )
        row = conn.execute(
            "SELECT tokens_used FROM research_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return int(row["tokens_used"]) if row else 0


def request_cancel(run_id: str) -> bool:
    with get_db_conn() as conn:
        cur = conn.execute(
            "UPDATE research_runs SET cancel_requested = 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (run_id,),
        )
    return cur.rowcount > 0


def cancel_requested(run_id: str) -> bool:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT cancel_requested FROM research_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return bool(row and row["cancel_requested"])


# --- steps ------------------------------------------------------------------


def create_step(
    run_id: str,
    *,
    seq: int,
    kind: str,
    name: str,
    input: dict[str, Any] | None = None,
    max_attempts: int = 3,
    round: int = 0,
) -> dict[str, Any]:
    step_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO research_steps
                (id, run_id, seq, kind, name, input, max_attempts, round)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step_id,
                run_id,
                seq,
                kind,
                name,
                json.dumps(input or {}),
                max_attempts,
                round,
            ),
        )
    step = get_step(step_id)
    assert step is not None
    return step


def set_step_output(step_id: str, output: dict[str, Any]) -> None:
    """Overwrite a completed step's output without touching its status.

    Exists for the plan-approval gate. `_pipeline` reads the plan back from the plan
    *step's* output on resume, while `update_run(plan=…)` writes the run row — so an
    edited plan persisted only to the run would be silently ignored the moment the
    run resumed. Both are written; this is the half that matters.
    """
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE research_steps SET output = ? WHERE id = ?",
            (json.dumps(output), step_id),
        )


def get_step(
    step_id: str, *, include_transcript: bool = False
) -> dict[str, Any] | None:
    with get_db_conn() as conn:
        r = conn.execute(
            "SELECT * FROM research_steps WHERE id = ?", (step_id,)
        ).fetchone()
    return _step_row(r, include_transcript=include_transcript) if r else None


def list_steps(
    run_id: str, *, include_transcript: bool = False
) -> list[dict[str, Any]]:
    init_research_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM research_steps WHERE run_id = ? ORDER BY seq ASC, id ASC",
            (run_id,),
        ).fetchall()
    return [_step_row(r, include_transcript=include_transcript) for r in rows]


def mark_step_running(step_id: str) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE research_steps SET status = 'running', attempt = attempt + 1, "
            "started_at = COALESCE(started_at, CURRENT_TIMESTAMP), error = NULL "
            "WHERE id = ?",
            (step_id,),
        )


def finish_step(
    step_id: str,
    *,
    status: str,
    output: dict[str, Any] | None = None,
    transcript: list[dict[str, Any]] | None = None,
    tokens_used: int | None = None,
    error: str | None = None,
) -> None:
    """Persist a step's result. Output + transcript land in the same UPDATE that
    flips the status — the checkpoint is atomic, there is no marked-done-but-
    unsaved window."""
    if status not in ("done", "failed", "skipped", "pending"):
        raise ValueError(f"not a terminal/reset step status: {status}")
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE research_steps SET status = ?, "
            "output = COALESCE(?, output), transcript = COALESCE(?, transcript), "
            "tokens_used = COALESCE(?, tokens_used), error = ?, "
            "finished_at = CASE WHEN ? IN ('done','failed','skipped') "
            "THEN CURRENT_TIMESTAMP ELSE NULL END "
            "WHERE id = ?",
            (
                status,
                json.dumps(output) if output is not None else None,
                json.dumps(transcript) if transcript is not None else None,
                tokens_used,
                error,
                status,
                step_id,
            ),
        )


def reset_running_steps(run_id: str) -> int:
    """Crash recovery: a step stuck `running` was in flight when the process died.
    Back to `pending`, keeping the attempt count (the retry budget survives)."""
    with get_db_conn() as conn:
        cur = conn.execute(
            "UPDATE research_steps SET status = 'pending' "
            "WHERE run_id = ? AND status = 'running'",
            (run_id,),
        )
    return cur.rowcount


def reset_failed_steps(run_id: str) -> int:
    """Manual retry: failed steps get a fresh attempt budget."""
    with get_db_conn() as conn:
        cur = conn.execute(
            "UPDATE research_steps SET status = 'pending', attempt = 0, error = NULL "
            "WHERE run_id = ? AND status = 'failed'",
            (run_id,),
        )
    return cur.rowcount


# --- follow-ups -------------------------------------------------------------
#
# The transport for mid-run steering. A follow-up is a row rather than an in-memory
# signal so it survives a restart and so the route that writes it doesn't need a
# handle on the worker running the run.


def add_followup(run_id: str, text: str) -> dict[str, Any]:
    init_research_db()
    followup_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO research_followups (id, run_id, text) VALUES (?, ?, ?)",
            (followup_id, run_id, text),
        )
    return {"id": followup_id, "run_id": run_id, "text": text}


def list_followups(
    run_id: str, *, unconsumed_only: bool = False
) -> list[dict[str, Any]]:
    init_research_db()
    sql = "SELECT * FROM research_followups WHERE run_id = ?"
    if unconsumed_only:
        sql += " AND consumed_at IS NULL"
    sql += " ORDER BY created_at ASC"
    with get_db_conn() as conn:
        rows = conn.execute(sql, (run_id,)).fetchall()
    return [
        {
            "id": r["id"],
            "run_id": r["run_id"],
            "text": r["text"],
            "created_at": str(r["created_at"]),
            "consumed_at": str(r["consumed_at"]) if r["consumed_at"] else None,
        }
        for r in rows
    ]


def consume_followups(ids: list[str]) -> None:
    if not ids:
        return
    with get_db_conn() as conn:
        conn.executemany(
            "UPDATE research_followups SET consumed_at = CURRENT_TIMESTAMP WHERE id = ?",
            [(i,) for i in ids],
        )


# --- tool calls -------------------------------------------------------------
#
# One tiny row per subagent tool call. "What did subagent 3 actually search for" is
# the single most useful thing to know when a run comes back thin, and digging it out
# of a step's transcript JSON blob is miserable.


def record_tool_call(
    run_id: str,
    step_id: str,
    *,
    seq: int,
    name: str,
    args: dict[str, Any] | None,
    ok: bool,
    ms: int | None,
    summary: str,
) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO research_tool_calls "
            "(id, run_id, step_id, seq, name, args, ok, ms, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                run_id,
                step_id,
                seq,
                name,
                json.dumps(args or {})[:2_000],
                1 if ok else 0,
                ms,
                summary[:400],
            ),
        )


def list_tool_calls(run_id: str, limit: int = 500) -> list[dict[str, Any]]:
    init_research_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM research_tool_calls WHERE run_id = ? "
            "ORDER BY created_at ASC, seq ASC LIMIT ?",
            (run_id, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            args = json.loads(r["args"] or "{}")
        except ValueError:
            args = {}
        out.append(
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "step_id": r["step_id"],
                "seq": r["seq"],
                "name": r["name"],
                "args": args,
                "ok": bool(r["ok"]),
                "ms": r["ms"],
                "summary": r["summary"],
                "created_at": str(r["created_at"]),
            }
        )
    return out
