"""The friends roster: `social_friends` + `social_devices` in the app database.

The roster lives **locally**, in `$HORRIBLE_DATA_DIR/app.db`, not on a server. A
friendship here is a pair of signed public keys this node has chosen to trust, so
it keeps working on a LAN with no internet and survives any directory service going
away. A directory is only ever consulted to answer "what address is this person at
right now" — never "who are my friends".

Two tables because the cardinality genuinely differs: one row per *person* in
`social_friends`, one row per *machine* in `social_devices`, joined on `person_id`.
That is what lets one human with a desktop and a laptop occupy a single row in the
Friends panel while still being individually dialable.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.social.friendcode import format_friend_code
from backend.modules.social.models import DeviceInfo, Friend, FriendStatus


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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add `column` to `table` if an older install lacks it.

    The probe `init_social_db`'s docstring warns about: `CREATE TABLE IF NOT EXISTS`
    is a no-op on an existing table, so a column added after this shipped reaches
    only fresh installs unless something explicitly ALTERs. Reading `PRAGMA
    table_info` rather than catching the duplicate-column error keeps a real failure
    (a locked or corrupt database) loud.
    """
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_social_db() -> None:
    """Create the roster tables (idempotent).

    `CREATE TABLE IF NOT EXISTS` never adds a column to a table that already
    exists, so any field added after this ships needs an explicit `ALTER` probe
    here — the same trap `init_research_db` documents. See `_ensure_column`.
    """
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_friends (
                person_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                person_public_key TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT,
                is_self INTEGER NOT NULL DEFAULT 0,
                added_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_devices (
                node_id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                node_public_key TEXT NOT NULL,
                label TEXT NOT NULL,
                cert TEXT,
                last_address TEXT,
                last_seen REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_devices_person "
            "ON social_devices(person_id)"
        )
        # The person's *ladder* identity, cached from the game server's directory.
        #
        # Nullable on purpose, and a null is not an error: a friend who has never
        # signed in to the game server is still a perfectly good fabric friend, and
        # the roster renders them without a callsign rather than hiding them. The
        # cache exists so the Friends list can name people with the game server
        # unreachable — the roster is local and must not acquire a network
        # dependency to render.
        _ensure_column(conn, "social_friends", "handle", "TEXT")
        _ensure_column(conn, "social_friends", "account_id", "TEXT")
        # When the ladder link was last confirmed, so a stale cache can be refreshed
        # without re-resolving every friend on every roster build.
        _ensure_column(conn, "social_friends", "ladder_synced_at", "REAL")


# ---- friends ----------------------------------------------------------------------


def upsert_friend(
    person_id: str,
    *,
    display_name: str | None = None,
    person_public_key: str | None = None,
    status: FriendStatus | None = None,
    note: str | None = None,
    is_self: bool | None = None,
) -> None:
    """Insert or update one roster row, leaving unspecified fields untouched.

    Written as read-modify-write rather than `INSERT … ON CONFLICT` because callers
    routinely want to change exactly one field (a status transition, a renamed
    contact) without having to restate the rest of the row.

    The ladder cache (`handle` / `account_id`) is deliberately *not* settable here —
    it is written only by `set_ladder_identity`, which is the one caller that has
    verified the binding. Folding it into the general-purpose upsert would let any
    caller assert a callsign for a person.
    """
    now = time.time()
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM social_friends WHERE person_id = ?", (person_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO social_friends (person_id, display_name, "
                "person_public_key, status, note, is_self, added_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    person_id,
                    display_name or person_id,
                    person_public_key or "",
                    status or "pending_out",
                    note,
                    int(bool(is_self)),
                    now,
                    now,
                ),
            )
            return
        conn.execute(
            "UPDATE social_friends SET display_name = ?, person_public_key = ?, "
            "status = ?, note = ?, is_self = ?, updated_at = ? WHERE person_id = ?",
            (
                display_name if display_name is not None else row["display_name"],
                person_public_key
                if person_public_key is not None
                else row["person_public_key"],
                status if status is not None else row["status"],
                note if note is not None else row["note"],
                int(bool(is_self)) if is_self is not None else row["is_self"],
                now,
                person_id,
            ),
        )


def set_ladder_identity(
    person_id: str, *, handle: str | None, account_id: str | None
) -> None:
    """Cache the game-server identity a directory lookup proved for `person_id`.

    Only ever called with an entry whose `person_id` was checked against the
    fingerprint of the key it arrived with (`handles.resolve` / `ladder.resolve_person`),
    so a hostile directory can withhold a binding but not invent one. No-ops when
    the person isn't on the roster: learning a stranger's callsign is not a reason
    to create a friend row.
    """
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE social_friends SET handle = ?, account_id = ?, "
            "ladder_synced_at = ? WHERE person_id = ?",
            (handle, account_id, time.time(), person_id),
        )


def friends_missing_ladder_identity() -> list[dict[str, Any]]:
    """Roster rows with no cached callsign yet — the reconciliation worklist."""
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM social_friends WHERE account_id IS NULL AND is_self = 0"
        ).fetchall()
    return [dict(r) for r in rows]


def person_for_account(account_id: str) -> str | None:
    """Reverse the cache: which roster person is this game-server account?

    The lookup the ladder→fabric direction needs — a `friends` frame from the game
    server names accounts, and the roster keys on people.
    """
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT person_id FROM social_friends WHERE account_id = ?", (account_id,)
        ).fetchone()
    return str(row["person_id"]) if row is not None else None


def get_friend_row(person_id: str) -> dict[str, Any] | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM social_friends WHERE person_id = ?", (person_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def remove_friend(person_id: str) -> None:
    with get_db_conn() as conn:
        conn.execute("DELETE FROM social_friends WHERE person_id = ?", (person_id,))
        conn.execute("DELETE FROM social_devices WHERE person_id = ?", (person_id,))


def set_status(person_id: str, status: FriendStatus) -> None:
    upsert_friend(person_id, status=status)


# ---- devices ----------------------------------------------------------------------


def upsert_device(
    node_id: str,
    person_id: str,
    node_public_key: str,
    label: str,
    cert: dict[str, Any] | None = None,
    address: str | None = None,
) -> None:
    with get_db_conn() as conn:
        existing = conn.execute(
            "SELECT cert, last_address FROM social_devices WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        cert_json = json.dumps(cert) if cert is not None else None
        if existing is not None:
            # Keep the stored certificate and address when this update doesn't
            # carry fresh ones — a presence ping shouldn't erase how to dial a peer.
            cert_json = cert_json if cert is not None else existing["cert"]
            address = address if address is not None else existing["last_address"]
        conn.execute(
            "INSERT INTO social_devices (node_id, person_id, node_public_key, label, "
            "cert, last_address, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(node_id) DO UPDATE SET person_id = excluded.person_id, "
            "node_public_key = excluded.node_public_key, label = excluded.label, "
            "cert = excluded.cert, last_address = excluded.last_address, "
            "last_seen = excluded.last_seen",
            (
                node_id,
                person_id,
                node_public_key,
                label,
                cert_json,
                address,
                time.time(),
            ),
        )


def list_devices(person_id: str) -> list[dict[str, Any]]:
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM social_devices WHERE person_id = ? ORDER BY label",
            (person_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def person_for_node(node_id: str) -> str | None:
    """Which person a machine belongs to, if this node has seen a cert for it.

    The reverse lookup the peer fabric needs: an envelope arrives tagged with a
    `node_id`, and the social layer has to decide whose it is.
    """
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT person_id FROM social_devices WHERE node_id = ?", (node_id,)
        ).fetchone()
    return str(row["person_id"]) if row is not None else None


def remove_device(node_id: str) -> None:
    with get_db_conn() as conn:
        conn.execute("DELETE FROM social_devices WHERE node_id = ?", (node_id,))


# ---- assembly ---------------------------------------------------------------------


def device_info(row: dict[str, Any], online_nodes: set[str]) -> DeviceInfo:
    return DeviceInfo(
        node_id=row["node_id"],
        person_id=row["person_id"],
        node_public_key=row["node_public_key"],
        label=row["label"],
        online=row["node_id"] in online_nodes,
        last_seen=row["last_seen"],
        last_address=row["last_address"],
    )


def build_friend(row: dict[str, Any], online_nodes: set[str]) -> Friend:
    """Assemble one roster row into the shape the browser renders.

    `presence` is computed here rather than stored: a person is online exactly when
    one of their machines currently has a live session, which is a fact about the
    hub's connection table, not about the database.
    """
    devices = [device_info(d, online_nodes) for d in list_devices(row["person_id"])]
    return Friend(
        person_id=row["person_id"],
        display_name=row["display_name"],
        friend_code=format_friend_code(row["person_id"]),
        person_public_key=row["person_public_key"],
        status=row["status"],
        note=row["note"],
        added_at=row["added_at"],
        presence="online" if any(d.online for d in devices) else "offline",
        devices=devices,
        is_self=bool(row["is_self"]),
        # `.get`, not `[...]`: these columns arrive by ALTER, and a row read through
        # an older code path (or a test fixture building a dict by hand) legitimately
        # won't carry them.
        handle=row.get("handle"),
        account_id=row.get("account_id"),
    )


def list_friends(online_nodes: set[str] | None = None) -> list[Friend]:
    online = online_nodes or set()
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM social_friends ORDER BY is_self DESC, display_name"
        ).fetchall()
    return [build_friend(dict(r), online) for r in rows]
