"""Durable storage for agent turns — the `agent_turns` table in `app.db`.

The recorder's `deque(maxlen=25)` is the *live* view: cheap, in-process, and exactly
right for the pane, which only ever shows recent turns. It is the wrong thing for two
other jobs — asking what the agent did yesterday, and serving trajectories to an
external client over MCP — because it holds 25 turns and empties on restart.

So this is a **write-through cache arrangement, not a replacement**: the recorder keeps
its ring for the pane and additionally persists each turn here. The ring stays the fast
path; this table is the durable one. Nothing reads the database to render the pane, so a
slow or failed write can never stall a turn.

**Failures are swallowed on purpose.** Recording is observation; observation must not
break the thing it observes. Every write is wrapped and logged, exactly as
`capture_round` already swallows its own failures.

Rounds are stored as a JSON blob rather than a child table. They are only ever read
whole (a turn's context is meaningless one round at a time), the shape is versioned by
the pydantic models, and a `rounds` table would need a migration every time
`RoundSnapshot` gains a field.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.interpretability.models import TurnSnapshot

logger = logging.getLogger(__name__)

# How many turns to keep. Generous — a row is a few KB of clipped previews, so even a
# heavy week is single-digit MB, and the whole point of persisting is to be able to
# look back further than the ring.
MAX_STORED_TURNS = 5000


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


def init_turn_db() -> None:
    """Create the turns table (idempotent)."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_turns (
                turn_id TEXT PRIMARY KEY,
                parent_turn_id TEXT,
                agent_id TEXT NOT NULL DEFAULT 'main',
                agent_name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'local',
                model TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                started_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0,
                rounds INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                peer_id TEXT,
                snapshot TEXT NOT NULL
            )
            """
        )
        # Listing is always "most recent first", and the tree walk is by parent.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_turns_started ON agent_turns(started_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_turns_parent ON agent_turns(parent_turn_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_turns_agent ON agent_turns(agent_id)"
        )


def _totals(turn: TurnSnapshot) -> tuple[int, int]:
    """(round count, tokens in the last round).

    The *last* round, not the sum: rounds are cumulative — each one resends the whole
    conversation plus what the loop appended. Summing them would multiply-count the
    same context and report a turn as costing several times what it did.
    """
    if not turn.rounds:
        return 0, 0
    return len(turn.rounds), turn.rounds[-1].totalTokens


def save_turn(turn: TurnSnapshot) -> None:
    """Insert or replace one turn. Never raises — see the module docstring."""
    try:
        init_turn_db()
        rounds, tokens = _totals(turn)
        with get_db_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_turns (
                    turn_id, parent_turn_id, agent_id, agent_name, kind, model,
                    provider, started_at, updated_at, rounds, total_tokens, peer_id,
                    snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    rounds = excluded.rounds,
                    total_tokens = excluded.total_tokens,
                    snapshot = excluded.snapshot
                """,
                (
                    turn.turnId,
                    turn.parentTurnId,
                    turn.agentId,
                    turn.agentName,
                    turn.kind,
                    turn.model,
                    turn.provider,
                    turn.startedAt,
                    time.time(),
                    rounds,
                    tokens,
                    turn.peerId,
                    turn.model_dump_json(),
                ),
            )
    except Exception:
        logger.exception("interpretability: failed to persist turn %s", turn.turnId)


def prune(keep: int = MAX_STORED_TURNS) -> int:
    """Drop the oldest turns beyond `keep`. Returns how many were removed."""
    try:
        init_turn_db()
        with get_db_conn() as conn:
            cursor = conn.execute(
                """
                DELETE FROM agent_turns WHERE turn_id IN (
                    SELECT turn_id FROM agent_turns
                    ORDER BY started_at DESC LIMIT -1 OFFSET ?
                )
                """,
                (keep,),
            )
            return cursor.rowcount or 0
    except Exception:
        logger.exception("interpretability: prune failed")
        return 0


def _summary_row(r: sqlite3.Row) -> dict[str, Any]:
    """A turn's metadata without its context blocks.

    This is the shape trajectory *listings* return — no prompt text, no tool results.
    A caller that wants content asks for one turn explicitly, which is also the
    boundary the MCP export redacts at.
    """
    return {
        "turnId": r["turn_id"],
        "parentTurnId": r["parent_turn_id"],
        "agentId": r["agent_id"],
        "agentName": r["agent_name"],
        "kind": r["kind"],
        "model": r["model"],
        "provider": r["provider"],
        "startedAt": r["started_at"],
        "rounds": r["rounds"],
        "totalTokens": r["total_tokens"],
        "peerId": r["peer_id"],
    }


def list_turns(
    limit: int = 50,
    *,
    agent_id: str | None = None,
    since: float | None = None,
    roots_only: bool = False,
) -> list[dict[str, Any]]:
    """Turn summaries, most recent first.

    `roots_only` hides delegated sub-turns, so a listing shows the turns a *user*
    started rather than interleaving them with the specialists they handed off to.
    """
    try:
        init_turn_db()
        clauses: list[str] = []
        params: list[Any] = []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(since)
        if roots_only:
            clauses.append("parent_turn_id IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        with get_db_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_turns {where} ORDER BY started_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_summary_row(r) for r in rows]
    except Exception:
        logger.exception("interpretability: list_turns failed")
        return []


def get_turn(turn_id: str) -> TurnSnapshot | None:
    """One turn in full, rounds included."""
    try:
        init_turn_db()
        with get_db_conn() as conn:
            row = conn.execute(
                "SELECT snapshot FROM agent_turns WHERE turn_id = ?", (turn_id,)
            ).fetchone()
        if row is None:
            return None
        return TurnSnapshot.model_validate(json.loads(row["snapshot"]))
    except Exception:
        logger.exception("interpretability: get_turn failed for %s", turn_id)
        return None


def get_children(turn_id: str) -> list[dict[str, Any]]:
    """Summaries of the turns delegated from `turn_id`."""
    try:
        init_turn_db()
        with get_db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_turns WHERE parent_turn_id = ? ORDER BY started_at",
                (turn_id,),
            ).fetchall()
        return [_summary_row(r) for r in rows]
    except Exception:
        logger.exception("interpretability: get_children failed for %s", turn_id)
        return []


def get_tree(turn_id: str, _depth: int = 0) -> dict[str, Any] | None:
    """One turn with its delegation subtree.

    Depth is bounded even though `agent.delegate` is one level deep by construction —
    a plugin agent with `can_delegate` set, or a corrupted `parent_turn_id`, must not
    be able to spin this into infinite recursion.
    """
    if _depth > 5:
        return None
    try:
        init_turn_db()
        with get_db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM agent_turns WHERE turn_id = ?", (turn_id,)
            ).fetchone()
        if row is None:
            return None
        node = _summary_row(row)
        node["children"] = [
            child
            for c in get_children(turn_id)
            if (child := get_tree(c["turnId"], _depth + 1)) is not None
        ]
        return node
    except Exception:
        logger.exception("interpretability: get_tree failed for %s", turn_id)
        return None


def stats() -> dict[str, Any]:
    """Aggregate counts over the stored trajectories."""
    try:
        init_turn_db()
        with get_db_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS turns,
                       COALESCE(SUM(rounds), 0) AS rounds,
                       COALESCE(MIN(started_at), 0) AS earliest,
                       COALESCE(MAX(started_at), 0) AS latest
                FROM agent_turns
                """
            ).fetchone()
            by_agent = conn.execute(
                "SELECT agent_id, COUNT(*) AS n FROM agent_turns GROUP BY agent_id"
            ).fetchall()
        return {
            "turns": row["turns"],
            "rounds": row["rounds"],
            "earliest": row["earliest"],
            "latest": row["latest"],
            "byAgent": {r["agent_id"]: r["n"] for r in by_agent},
        }
    except Exception:
        logger.exception("interpretability: stats failed")
        return {"turns": 0, "rounds": 0, "earliest": 0, "latest": 0, "byAgent": {}}


def clear() -> None:
    """Drop every stored turn (the pane's Clear button, and tests)."""
    try:
        init_turn_db()
        with get_db_conn() as conn:
            conn.execute("DELETE FROM agent_turns")
    except Exception:
        logger.exception("interpretability: clear failed")
