"""Notification rules: what gets through, and what the agent is watching for.

Two tables, both in `$HORRIBLE_DATA_DIR/app.db`, both deliberately local — a mute
is a statement about *this* person's attention, and it should survive every server
going away.

**`notification_mutes`** — the "quiet down" half. A mute is scoped by category
(`message`, `presence`, …) and optionally by person, and carries an `expires_at`
so *"mute messages for a bit"* is a thing that can actually be said. The `except_person`
column is what makes *"mute everything except Andrew"* one rule instead of a mute
per friend plus a promise to remember to undo them.

**`agent_watches`** — the "tell me when" half. A standing instruction, evaluated
against events rather than a clock: `kind` names the event, `subject` names who it
is about, and `one_shot` decides whether it survives firing.

**`notifications`** — the inbox, and the reason a missed toast is harmless. A toast
is a *cache*: it shows for four seconds and then it is gone, and until this table
existed so was the notification, because the feed behind the bell lived in a
JavaScript array that a page reload emptied. Anything that arrived while the app
was closed, or while you were reading something else and then refreshed, was
simply never seen. A notification is now written here first and rendered from
here, so every surface is a *view* of one durable row rather than the only copy.

`dedupe` is what makes those surfaces agree. One invite reaches the shell toast,
the bell, the in-game overlay and an OS notification; without a shared key,
accepting it in one place leaves three stale copies of it elsewhere, and a
re-sent invite stacks instead of refreshing.

Nothing like either existed before: the only `mute` in the repo was a microphone,
and there was no watch/rule/trigger table anywhere, so an instruction that outlived
its own chat turn had nothing to be written down in.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir

#: Categories a rule can talk about. Closed on purpose: an open vocabulary means a
#: typo in an agent-authored rule silently mutes nothing, forever, with no error.
#: `watch` is here because a fired watch is delivered as a notification like any
#: other, and a category the producer emits but no rule can name would be one the
#: user is unable to silence short of muting everything. `review` is the same case:
#: an agent filing a record proposal needs to reach the user who is looking at
#: something else, and an unattended extraction run must be muteable.
CATEGORIES = (
    "message",
    "presence",
    "invite",
    "friend_request",
    "watch",
    "review",
    "all",
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


def init_notifications_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_mutes (
                id             TEXT PRIMARY KEY,
                category       TEXT NOT NULL,
                person_id      TEXT,          -- NULL = the whole category
                except_person  TEXT,          -- "…except from this person"
                reason         TEXT,
                created_at     REAL NOT NULL,
                expires_at     REAL           -- NULL = until explicitly lifted
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_watches (
                id         TEXT PRIMARY KEY,
                kind       TEXT NOT NULL,     -- 'presence'
                subject    TEXT,              -- person_id, or NULL for "anyone"
                label      TEXT NOT NULL,     -- how to name them back to the user
                predicate  TEXT NOT NULL,     -- json: {"online": true}
                note       TEXT,              -- what the user actually asked for
                one_shot   INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                expires_at REAL,
                fired_at   REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_watches_kind ON agent_watches(kind)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id         TEXT PRIMARY KEY,
                category   TEXT NOT NULL,
                kind       TEXT NOT NULL,     -- info | success | warning | error
                title      TEXT NOT NULL,
                body       TEXT NOT NULL,
                person_id  TEXT,              -- who it is *about*, when that applies
                data       TEXT NOT NULL,     -- json: the payload a surface acts on
                dedupe     TEXT,              -- one key across every surface
                created_at REAL NOT NULL,
                expires_at REAL,              -- NULL = keep until read/dismissed
                read_at    REAL,
                cleared_at REAL
            )
            """
        )
        # Partial: a cleared row is history, and the feed never asks for it.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_live "
            "ON notifications(created_at DESC) WHERE cleared_at IS NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedupe "
            "ON notifications(dedupe) WHERE dedupe IS NOT NULL AND cleared_at IS NULL"
        )


# ---- mutes -------------------------------------------------------------------------


def add_mute(
    category: str,
    *,
    person_id: str | None = None,
    except_person: str | None = None,
    duration_s: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Silence a category (optionally only one person, or everyone but one)."""
    init_notifications_db()
    now = time.time()
    row = {
        "id": uuid.uuid4().hex,
        "category": category,
        "person_id": person_id,
        "except_person": except_person,
        "reason": reason,
        "created_at": now,
        "expires_at": now + duration_s if duration_s else None,
    }
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO notification_mutes (id, category, person_id, except_person,"
            " reason, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                category,
                person_id,
                except_person,
                reason,
                now,
                row["expires_at"],
            ),
        )
    return row


def active_mutes() -> list[dict[str, Any]]:
    """Live mutes, expired ones swept on the way past.

    Sweeping on read rather than on a timer: there is no other clock in this
    module, and a rule that has expired is indistinguishable from one that was
    never there — so the cheapest correct moment to drop it is when someone asks.
    """
    init_notifications_db()
    now = time.time()
    with get_db_conn() as conn:
        conn.execute(
            "DELETE FROM notification_mutes WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        rows = conn.execute(
            "SELECT * FROM notification_mutes ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def clear_mutes(category: str | None = None, person_id: str | None = None) -> int:
    """Lift mutes. No arguments lifts everything; returns how many went."""
    init_notifications_db()
    clauses, params = [], []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if person_id:
        clauses.append("person_id = ?")
        params.append(person_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db_conn() as conn:
        cur = conn.execute(f"DELETE FROM notification_mutes{where}", params)  # noqa: S608 — clauses are literals, values are bound
        return cur.rowcount


def is_muted(category: str, person_id: str | None = None) -> bool:
    """Whether a notification of `category` about `person_id` should be suppressed.

    Checked at the **producer**, before anything is sent — a muted notification
    that still crosses the socket and is dropped in the browser is a notification
    that still lit up your phone.

    `except_person` inverts the scope: a rule with it set mutes the category for
    *everyone but* that person, which is the shape "mute everything except Andrew"
    actually needs.
    """
    for mute in active_mutes():
        if mute["category"] not in (category, "all"):
            continue
        exempt = mute["except_person"]
        if exempt:
            # An "all but X" rule: silent unless this is X.
            if person_id != exempt:
                return True
            continue
        scope = mute["person_id"]
        if scope is None or scope == person_id:
            return True
    return False


# ---- watches -----------------------------------------------------------------------


def add_watch(
    kind: str,
    *,
    subject: str | None,
    label: str,
    predicate: dict[str, Any],
    note: str | None = None,
    one_shot: bool = True,
    duration_s: float | None = None,
) -> dict[str, Any]:
    init_notifications_db()
    now = time.time()
    watch_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO agent_watches (id, kind, subject, label, predicate, note,"
            " one_shot, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                watch_id,
                kind,
                subject,
                label,
                json.dumps(predicate),
                note,
                int(one_shot),
                now,
                now + duration_s if duration_s else None,
            ),
        )
    return {
        "id": watch_id,
        "kind": kind,
        "subject": subject,
        "label": label,
        "predicate": predicate,
        "note": note,
        "one_shot": one_shot,
        "created_at": now,
    }


def list_watches(kind: str | None = None) -> list[dict[str, Any]]:
    """Live watches, expired and already-fired one-shots swept on the way past."""
    init_notifications_db()
    now = time.time()
    with get_db_conn() as conn:
        conn.execute(
            "DELETE FROM agent_watches WHERE (expires_at IS NOT NULL AND expires_at <= ?)"
            " OR (one_shot = 1 AND fired_at IS NOT NULL)",
            (now,),
        )
        sql = "SELECT * FROM agent_watches"
        params: list[Any] = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        rows = conn.execute(sql + " ORDER BY created_at DESC", params).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["predicate"] = json.loads(item["predicate"])
        except ValueError:
            item["predicate"] = {}
        out.append(item)
    return out


def mark_fired(watch_id: str) -> None:
    init_notifications_db()
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE agent_watches SET fired_at = ? WHERE id = ?",
            (time.time(), watch_id),
        )


def cancel_watch(watch_id: str) -> bool:
    init_notifications_db()
    with get_db_conn() as conn:
        cur = conn.execute("DELETE FROM agent_watches WHERE id = ?", (watch_id,))
        return cur.rowcount > 0


# ---- the inbox ---------------------------------------------------------------------
#
# Deliberately *after* the mute check rather than before it: `service.notify`
# filters first and writes second, so a muted notification leaves no row. A mute
# that still filled the bell would be a mute in name only.

#: How many live rows the feed hands back. A feed to glance at, not a log — and
#: the rows themselves are kept, so raising this later shows more history rather
#: than resurrecting nothing.
FEED_LIMIT = 100


def record(
    category: str,
    kind: str,
    title: str,
    body: str,
    *,
    person_id: str | None = None,
    data: dict[str, Any] | None = None,
    dedupe: str | None = None,
    expires_at: float | None = None,
) -> dict[str, Any]:
    """Write one notification and return it as the wire shape.

    A repeat of a `dedupe` key **replaces** the live row rather than adding one.
    That is what makes re-inviting somebody who has not answered yet refresh their
    invite instead of stacking a second identical card under the first.
    """
    now = time.time()
    row_id = uuid.uuid4().hex[:12]
    payload = json.dumps(data or {})
    with get_db_conn() as conn:
        if dedupe:
            existing = conn.execute(
                "SELECT id FROM notifications WHERE dedupe = ? AND cleared_at IS NULL",
                (dedupe,),
            ).fetchone()
            if existing is not None:
                row_id = str(existing["id"])
                conn.execute(
                    "UPDATE notifications SET category = ?, kind = ?, title = ?, "
                    "body = ?, person_id = ?, data = ?, created_at = ?, "
                    "expires_at = ?, read_at = NULL WHERE id = ?",
                    (
                        category,
                        kind,
                        title,
                        body,
                        person_id,
                        payload,
                        now,
                        expires_at,
                        row_id,
                    ),
                )
                return _view(row_id, category, kind, title, body, person_id, data, dedupe, now)
        conn.execute(
            "INSERT INTO notifications (id, category, kind, title, body, person_id, "
            "data, dedupe, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                row_id,
                category,
                kind,
                title,
                body,
                person_id,
                payload,
                dedupe,
                now,
                expires_at,
            ),
        )
    return _view(row_id, category, kind, title, body, person_id, data, dedupe, now)


def _view(
    row_id: str,
    category: str,
    kind: str,
    title: str,
    body: str,
    person_id: str | None,
    data: dict[str, Any] | None,
    dedupe: str | None,
    at: float,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "category": category,
        "kind": kind,
        "title": title,
        "body": body,
        "person_id": person_id,
        "dedupe": dedupe,
        "at": at,
        "read": False,
        **(data or {}),
    }


def feed(limit: int = FEED_LIMIT) -> list[dict[str, Any]]:
    """The live inbox, newest first — what the bell hydrates from at boot.

    Expired rows are swept on read rather than on a timer, the same way
    `fabric.live_invites` prunes: nothing needs waking up to keep a short list
    tidy, and an expiry that only matters when somebody looks can be evaluated
    when somebody looks.
    """
    now = time.time()
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE notifications SET cleared_at = ? "
            "WHERE cleared_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?",
            (now, now),
        )
        rows = conn.execute(
            "SELECT * FROM notifications WHERE cleared_at IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError):
            data = {}
        out.append(
            {
                "id": row["id"],
                "category": row["category"],
                "kind": row["kind"],
                "title": row["title"],
                "body": row["body"],
                "person_id": row["person_id"],
                "dedupe": row["dedupe"],
                "at": row["created_at"],
                "read": row["read_at"] is not None,
                **(data if isinstance(data, dict) else {}),
            }
        )
    return out


def mark_read(notification_id: str | None = None) -> None:
    """Mark one notification read, or all of them when given nothing.

    Read state lives here rather than in the browser so it is the same on every
    surface and survives a reload — dismissing a toast on the desktop should not
    leave the phone still showing it as new.
    """
    now = time.time()
    with get_db_conn() as conn:
        if notification_id:
            conn.execute(
                "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
                (now, notification_id),
            )
        else:
            conn.execute(
                "UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (now,)
            )


def clear(notification_id: str | None = None, *, dedupe: str | None = None) -> int:
    """Retire a notification from every surface at once.

    By `dedupe` is the important form: it is how accepting an invite in the game
    also clears the toast, the bell entry and the OS notification, rather than the
    other three going stale while the person wonders whether they answered it.
    """
    now = time.time()
    with get_db_conn() as conn:
        if dedupe:
            cur = conn.execute(
                "UPDATE notifications SET cleared_at = ? "
                "WHERE dedupe = ? AND cleared_at IS NULL",
                (now, dedupe),
            )
        elif notification_id:
            cur = conn.execute(
                "UPDATE notifications SET cleared_at = ? "
                "WHERE id = ? AND cleared_at IS NULL",
                (now, notification_id),
            )
        else:
            cur = conn.execute(
                "UPDATE notifications SET cleared_at = ? WHERE cleared_at IS NULL",
                (now,),
            )
        return cur.rowcount
