"""The one thing agentpedia owns: the fork edge.

The module's rule is that it owns no store, and this is the deliberate exception —
so it is worth saying exactly what is and is not in here. A fork *run* is not
stored: it goes through the ordinary `run_agent_loop`, so `agent_turns` records
what it was shown, the telemetry ring records what it sent, and `traj_runs` records
what it did, all under the fork's own `turn_id`. It opens in the stepper like any
other turn.

What none of them record is the **edge** — that this turn is a counterfactual of
that one, produced by these edits, with the tools simulated. That is a new fact,
and there is nowhere else for it to live:

* `TurnSnapshot.parentTurnId` already means *delegated from*. Reusing it would put
  forks in the delegation tree — hidden by `roots_only`, counted as sub-agents by
  `get_children` — which is a different claim about what happened.
* `traj_runs.meta` would have been the plan's home for it, and it is the right
  shape, but trajectory capture is **off by default and dataset-scoped**. A fork
  taken on a node that never turned capture on would leave no edge at all, and the
  Forks section would be empty for the ordinary user.

So: one table, three real columns and a JSON blob, in the same `app.db` as
everything else. Writes swallow, like every other recorder here — losing the edge
costs you a row in a list, and must never cost you the fork you just waited for.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.agentpedia.models import ForkRecord
from backend.modules.database.app_db import ensure_app_db_dir

logger = logging.getLogger(__name__)


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


def init_fork_db() -> None:
    """Create the fork table (idempotent)."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agentpedia_forks (
                fork_turn_id TEXT PRIMARY KEY,
                parent_turn_id TEXT NOT NULL,
                from_round INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0,
                live INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'complete',
                record TEXT NOT NULL
            )
            """
        )
        # Both listings this table has: everything newest-first, and the forks of
        # one turn (which is what the stepper asks for when a turn is open).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forks_created"
            " ON agentpedia_forks(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_forks_parent"
            " ON agentpedia_forks(parent_turn_id)"
        )


def save_fork(record: ForkRecord) -> None:
    """Insert or replace one fork edge. Never raises."""
    try:
        init_fork_db()
        with get_db_conn() as conn:
            conn.execute(
                """
                INSERT INTO agentpedia_forks (
                    fork_turn_id, parent_turn_id, from_round, created_at, live,
                    status, record
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fork_turn_id) DO UPDATE SET
                    status = excluded.status,
                    record = excluded.record
                """,
                (
                    record.fork_turn_id,
                    record.parent_turn_id,
                    record.from_round,
                    record.created_at or time.time(),
                    1 if record.live else 0,
                    record.status,
                    record.model_dump_json(),
                ),
            )
    except Exception:
        logger.exception("agentpedia: failed to persist fork %s", record.fork_turn_id)


def _record(row: sqlite3.Row) -> ForkRecord | None:
    try:
        return ForkRecord.model_validate(json.loads(row["record"]))
    except Exception:
        logger.exception("agentpedia: unreadable fork row %s", row["fork_turn_id"])
        return None


def list_forks(
    limit: int = 100, *, parent_turn_id: str | None = None
) -> list[ForkRecord]:
    """Fork edges, newest first."""
    try:
        init_fork_db()
        params: list[Any] = []
        where = ""
        if parent_turn_id:
            where = "WHERE parent_turn_id = ?"
            params.append(parent_turn_id)
        params.append(max(1, min(limit, 500)))
        with get_db_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM agentpedia_forks {where}"
                " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [record for row in rows if (record := _record(row)) is not None]
    except Exception:
        logger.exception("agentpedia: list_forks failed")
        return []


def get_fork(fork_turn_id: str) -> ForkRecord | None:
    try:
        init_fork_db()
        with get_db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM agentpedia_forks WHERE fork_turn_id = ?",
                (fork_turn_id,),
            ).fetchone()
        return _record(row) if row is not None else None
    except Exception:
        logger.exception("agentpedia: get_fork failed for %s", fork_turn_id)
        return None


def delete_fork(fork_turn_id: str) -> bool:
    """Forget the edge. The fork's own turn stays in `agent_turns` — this is the
    counterfactual *link* being dropped, not the run, and pruning another module's
    table from here would be reaching into a store agentpedia does not own."""
    try:
        init_fork_db()
        with get_db_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM agentpedia_forks WHERE fork_turn_id = ?", (fork_turn_id,)
            )
            return bool(cursor.rowcount)
    except Exception:
        logger.exception("agentpedia: delete_fork failed for %s", fork_turn_id)
        return False


def clear() -> None:
    """Drop every fork edge (tests)."""
    try:
        init_fork_db()
        with get_db_conn() as conn:
            conn.execute("DELETE FROM agentpedia_forks")
    except Exception:
        logger.exception("agentpedia: clear failed")
