"""The hub's own state: who has signed in, their sessions, WS tickets, and the
per-user instance token. One small SQLite file; nothing here is per-request hot
enough to need more.

Session ids and tickets are bearer secrets, so only their SHA-256 is stored — a
leaked `hub.db` cannot be replayed as a cookie. The games JWT is kept (the hub hands
it to the user's instance so games work there) and never leaves the server.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    handle TEXT,
    display_name TEXT,
    games_token TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS instances (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS placements (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data TEXT NOT NULL
);
"""

TICKET_SECONDS = 60.0


@dataclass(frozen=True)
class User:
    id: str
    handle: str | None
    display_name: str
    games_token: str

    def public(self) -> dict[str, Any]:
        """What the browser and the instance may see — never the token."""
        return {"id": self.id, "handle": self.handle, "display_name": self.display_name}


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class HubStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    # ---- users -------------------------------------------------------------

    def upsert_user(self, account: dict[str, Any], games_token: str) -> User:
        user_id = str(account.get("id") or "")
        if not user_id:
            raise ValueError("account has no id")
        handle = account.get("handle") or None
        display = str(account.get("display_name") or handle or user_id)
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO users (id, handle, display_name, games_token, created, updated)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET handle=excluded.handle,"
                " display_name=excluded.display_name, games_token=excluded.games_token,"
                " updated=excluded.updated",
                (user_id, handle, display, games_token, now, now),
            )
        return User(user_id, handle, display, games_token)

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, handle, display_name, games_token FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return User(*row) if row else None

    # ---- sessions ----------------------------------------------------------

    def create_session(self, user_id: str, ttl_seconds: float) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions (id_hash, user_id, expires) VALUES (?, ?, ?)",
                (_hash(sid), user_id, time.time() + ttl_seconds),
            )
        return sid

    def session_user(self, sid: str | None) -> User | None:
        if not sid:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT user_id, expires FROM sessions WHERE id_hash = ?",
                (_hash(sid),),
            ).fetchone()
        if not row or row[1] < time.time():
            return None
        return self.get_user(row[0])

    def delete_session(self, sid: str | None) -> None:
        if not sid:
            return
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE id_hash = ?", (_hash(sid),))

    # ---- WS tickets ----------------------------------------------------------

    def create_ticket(self, user_id: str) -> str:
        """A single-use, short-lived stand-in for the session cookie on a WebSocket
        upgrade that cannot carry it (a frontend on another origin, e.g. Vercel)."""
        ticket = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock, self._db:
            self._db.execute("DELETE FROM tickets WHERE expires < ?", (now,))
            self._db.execute(
                "INSERT INTO tickets (id_hash, user_id, expires) VALUES (?, ?, ?)",
                (_hash(ticket), user_id, now + TICKET_SECONDS),
            )
        return ticket

    def redeem_ticket(self, ticket: str | None) -> User | None:
        if not ticket:
            return None
        with self._lock, self._db:
            row = self._db.execute(
                "DELETE FROM tickets WHERE id_hash = ? RETURNING user_id, expires",
                (_hash(ticket),),
            ).fetchone()
        if not row or row[1] < time.time():
            return None
        return self.get_user(row[0])

    # ---- instances -----------------------------------------------------------

    def instance_token(self, user_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT token FROM instances WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row[0] if row else None

    def set_instance_token(self, user_id: str, token: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO instances (user_id, token, created) VALUES (?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET token=excluded.token",
                (user_id, token, time.time()),
            )

    # ---- placements -------------------------------------------------------------
    #
    # Where a spawner put the user's instance, for spawners that cannot find it again
    # by name: Fly addresses a machine and a volume by generated ids.

    def placement(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM placements WHERE user_id = ?", (user_id,)
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def set_placement(self, user_id: str, data: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO placements (user_id, data) VALUES (?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                (user_id, json.dumps(data)),
            )
