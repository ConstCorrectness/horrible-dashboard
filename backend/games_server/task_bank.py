"""The task bank: curated + generated code-game tasks, with played-tracking so
the server can hand a match a task **neither player has seen**.

Anti-cheat shape matches the challenge track: a task's `payload` (description,
buggy files, visible tests) is what players may see; its `hidden` half (hidden
tests, solutions) lives only server-side.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

from backend.games_server import store


def ensure_builtin() -> None:
    """Idempotently load the bundled starter tasks into the bank."""
    from backend.games_server.tasks_builtin import BUG_HUNT_TASKS

    store.init_db()
    with store.get_conn() as conn:
        for task in BUG_HUNT_TASKS:
            conn.execute(
                """
                INSERT INTO task_bank (id, kind, difficulty, payload, hidden, source, created_at)
                VALUES (?, ?, ?, ?, ?, 'builtin', ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload, hidden = excluded.hidden
                """,
                (
                    task["id"],
                    task["kind"],
                    task.get("difficulty", "standard"),
                    json.dumps(task["payload"]),
                    json.dumps(task["hidden"]),
                    time.time(),
                ),
            )


def add_task(
    task_id: str,
    kind: str,
    difficulty: str,
    payload: dict[str, Any],
    hidden: dict[str, Any],
    source: str = "generated",
) -> None:
    store.init_db()
    with store.get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_bank
                (id, kind, difficulty, payload, hidden, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                kind,
                difficulty,
                json.dumps(payload),
                json.dumps(hidden),
                source,
                time.time(),
            ),
        )


def pick_task(
    kind: str,
    difficulty: str,
    exclude_accounts: list[str],
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    """A random bank task of this kind/difficulty that none of `exclude_accounts`
    has played. Falls back to ignoring the difficulty, then to a repeat, so a
    match can always start; returns None only on an empty bank."""
    store.init_db()
    rng = rng or random.Random()
    with store.get_conn() as conn:
        placeholders = ",".join("?" for _ in exclude_accounts) or "''"
        base = (
            "SELECT * FROM task_bank WHERE kind = ? AND id NOT IN "
            f"(SELECT task_id FROM task_plays WHERE account_id IN ({placeholders}))"
        )
        rows = conn.execute(
            base + " AND difficulty = ?", (kind, *exclude_accounts, difficulty)
        ).fetchall()
        if not rows:
            rows = conn.execute(base, (kind, *exclude_accounts)).fetchall()
        if not rows:
            # Everyone has seen everything: allow repeats rather than blocking play.
            rows = conn.execute(
                "SELECT * FROM task_bank WHERE kind = ?", (kind,)
            ).fetchall()
    if not rows:
        return None
    row = rng.choice(rows)
    return {
        "id": row["id"],
        "kind": row["kind"],
        "difficulty": row["difficulty"],
        **json.loads(row["payload"]),
        **json.loads(row["hidden"]),
    }


def mark_played(accounts: list[str], task_id: str) -> None:
    store.init_db()
    with store.get_conn() as conn:
        for account_id in accounts:
            conn.execute(
                "INSERT OR IGNORE INTO task_plays (account_id, task_id, played_at) VALUES (?, ?, ?)",
                (account_id, task_id, time.time()),
            )
