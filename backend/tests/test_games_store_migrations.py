"""The game server store's versioned migration runner.

The live Fly volume upgrades by replaying the tail of `store.MIGRATIONS`, so these
tests cover the three cases that matter: a fresh database lands on the latest
version, a pre-migration (v0) database is upgraded in place without losing rows,
and re-running is a no-op.
"""

from __future__ import annotations

import sqlite3

from backend.games_server import store


def _columns(table: str) -> set[str]:
    with store.get_conn() as conn:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _version() -> int:
    with store.get_conn() as conn:
        return int(conn.execute("SELECT version FROM schema_meta").fetchone()[0])


def test_fresh_db_is_fully_migrated() -> None:
    store.init_db()
    assert _version() == len(store.MIGRATIONS)
    assert {"handle", "is_bot"} <= _columns("accounts")
    assert {"series_id", "ruleset", "models"} <= _columns("results")


def test_v0_db_upgrades_in_place_preserving_rows() -> None:
    # Recreate the pre-migration schema by hand: baseline tables, no schema_meta.
    path = store.get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE accounts (
            id TEXT PRIMARY KEY, provider TEXT NOT NULL, subject TEXT NOT NULL,
            display_name TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT NOT NULL,
            table_id TEXT NOT NULL, created_at REAL NOT NULL,
            winner INTEGER, payload TEXT NOT NULL
        );
        INSERT INTO accounts VALUES ('github:1', 'github', '1', 'Alice', 0.0);
        INSERT INTO results (game_id, table_id, created_at, winner, payload)
        VALUES ('tictactoe', 't1', 0.0, 0, '{}');
        """
    )
    conn.commit()
    conn.close()

    store.init_db()

    assert _version() == len(store.MIGRATIONS)
    assert {"handle", "is_bot"} <= _columns("accounts")
    assert {"series_id", "ruleset", "models"} <= _columns("results")
    with store.get_conn() as c:
        row = c.execute("SELECT * FROM accounts WHERE id = 'github:1'").fetchone()
        assert row["display_name"] == "Alice"
        assert row["is_bot"] == 0  # new column backfilled with its default
        # The one-time handle backfill derives a handle from the display name.
        assert row["handle"] == "alice"
        assert c.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1


def test_handle_backfill_resolves_collisions_and_skips_bots() -> None:
    # Fully migrated schema, then handleless accounts inserted straight (bypassing
    # the sign-in path): two whose usernames fold to the same base, plus a bot.
    store.init_db()
    with store.get_conn() as conn:
        conn.executemany(
            "INSERT INTO accounts "
            "(id, provider, subject, display_name, created_at, handle, is_bot) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?)",
            [
                ("github:1", "github", "1", "Mildred", 1.0, 0),
                ("google:2", "google", "2", "mildred", 2.0, 0),
                ("bot:ada", "bot", "ada", "Ada Bot", 3.0, 1),
            ],
        )
        store._m6_backfill_handles(conn)
        rows = {
            r["id"]: r["handle"]
            for r in conn.execute("SELECT id, handle FROM accounts").fetchall()
        }
    # Oldest-first: github:1 claims "mildred", google:2 gets the numeric suffix.
    assert rows["github:1"] == "mildred"
    assert rows["google:2"] == "mildred2"
    # Bots keep their NULL handle (they fall back to display_name in the UI).
    assert rows["bot:ada"] is None


def test_init_db_is_idempotent() -> None:
    store.init_db()
    first = _version()
    store.init_db()
    assert _version() == first == len(store.MIGRATIONS)
