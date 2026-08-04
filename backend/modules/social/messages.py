"""Direct messages, keyed by **person** and written to disk.

What this replaces: `network/chat.py` held conversations in an in-memory
`deque(maxlen=200)` keyed by `node_id`. Three consequences, all of which read as
the app being broken rather than as a design:

1. **Restarting the backend erased every conversation.**
2. **A friend on two machines was two conversations.** Message them from their
   laptop and reply to their desktop and the thread forked, because a node is a
   machine and a conversation is with a human.
3. **There was no unread count**, because nothing knew what you had read.

So the log lives here, in `app.db`, keyed by `person_id`, and `node_id` is
recorded only as *which machine it happened to arrive from* — useful for
diagnostics, never for grouping.

A message from someone who is not in the roster is still stored, under their
person id, with `person_id` resolved from the sending node's device row. If that
resolution fails there is no person to file it under, and the caller keeps the
node-keyed in-memory path — an unfiled message is better than a message dropped.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir

#: How much of a conversation the panel asks for on open. The full history stays
#: on disk; this is only the default page.
PAGE = 200


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


def init_messages_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_messages (
                id         TEXT PRIMARY KEY,
                person_id  TEXT NOT NULL,   -- who the conversation is with
                node_id    TEXT,            -- which machine it travelled over
                direction  TEXT NOT NULL,   -- 'in' | 'out'
                author     TEXT NOT NULL,   -- display name at the time it was sent
                body       TEXT NOT NULL,
                ts         REAL NOT NULL,
                read_at    REAL             -- NULL = unread (outbound is read on write)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_messages_person "
            "ON social_messages(person_id, ts)"
        )


def record(
    person_id: str,
    *,
    direction: str,
    author: str,
    body: str,
    node_id: str | None = None,
    ts: float | None = None,
    message_id: str | None = None,
    read: bool | None = None,
) -> dict[str, Any]:
    """Append one message to a conversation and return it in wire shape.

    Outbound messages are read the moment they are written — you cannot have an
    unread message you sent yourself.
    """
    init_messages_db()
    now = ts if ts is not None else time.time()
    row = {
        "id": message_id or uuid.uuid4().hex,
        "personId": person_id,
        "nodeId": node_id,
        "direction": direction,
        "from": author,
        "text": body,
        "ts": now,
        "read": direction == "out" if read is None else read,
    }
    with get_db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO social_messages (id, person_id, node_id, direction,"
            " author, body, ts, read_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                person_id,
                node_id,
                direction,
                author,
                body,
                now,
                now if row["read"] else None,
            ),
        )
    return row


def _view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "personId": row["person_id"],
        "nodeId": row["node_id"],
        "direction": row["direction"],
        "from": row["author"],
        "text": row["body"],
        "ts": row["ts"],
        "read": row["read_at"] is not None,
    }


def conversation(person_id: str, limit: int = PAGE) -> list[dict[str, Any]]:
    """The tail of a conversation, oldest first.

    Selected newest-first and reversed, so a long history returns the *recent*
    page rather than the first page from months ago.
    """
    init_messages_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM social_messages WHERE person_id = ? ORDER BY ts DESC LIMIT ?",
            (person_id, max(1, limit)),
        ).fetchall()
    return [_view(r) for r in reversed(rows)]


def unread_counts() -> dict[str, int]:
    """`person_id -> unread`, for the roster's badges. Only conversations with
    something unread appear."""
    init_messages_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT person_id, COUNT(*) AS n FROM social_messages"
            " WHERE direction = 'in' AND read_at IS NULL GROUP BY person_id"
        ).fetchall()
    return {r["person_id"]: int(r["n"]) for r in rows}


def mark_read(person_id: str) -> int:
    """Mark a whole conversation read. Returns how many were newly read."""
    init_messages_db()
    with get_db_conn() as conn:
        cur = conn.execute(
            "UPDATE social_messages SET read_at = ? WHERE person_id = ? AND read_at IS NULL",
            (time.time(), person_id),
        )
        return cur.rowcount


def last_messages() -> dict[str, dict[str, Any]]:
    """The most recent message per conversation, for a Steam-style list preview."""
    init_messages_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT m.* FROM social_messages m JOIN ("
            "  SELECT person_id, MAX(ts) AS ts FROM social_messages GROUP BY person_id"
            ") latest ON latest.person_id = m.person_id AND latest.ts = m.ts"
        ).fetchall()
    return {r["person_id"]: _view(r) for r in rows}


def clear(person_id: str) -> int:
    init_messages_db()
    with get_db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM social_messages WHERE person_id = ?", (person_id,)
        )
        return cur.rowcount
