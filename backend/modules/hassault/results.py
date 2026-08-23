"""Finished matches: what happened, written down.

Every number the post-match card shows used to be invented. `_watchdog_game_process`
waited for the client to exit and then called `random.randint` — kills, deaths,
headshots, damage, XP, rating, tier, level — and stashed the result in a
process-global dict that a backend restart emptied. The card was a screensaver
with a skin drop attached.

This is the other half of the fix. The simulation already knew all of it
(`MatchRoom.result_for`); this module gives those numbers somewhere to live:

- **A real table in `app.db`**, the same database the library catalog, browser
  history and karaoke queue use — so a match history survives a restart, and the
  database pane can query it like anything else (`SELECT * FROM hassault_matches`).
- **Progression derived from the row, never stored twice.** Level and total XP are
  a `SUM(xp)` over the table rather than a counter someone remembers to bump: a
  running total in its own column is a number that can disagree with the matches
  it is supposedly the sum of, and there is no way to tell which one is wrong.

### Provenance, and why it is a column

**Storage is not the trust boundary; simulation is.** A hassault match is
simulated by a `MatchRoom`, and when you host one that room is inside *your own*
backend — so the numbers are self-computed by construction. Moving the row to a
central database would not make it true; it would store a self-reported number
somewhere more official-looking, which is worse, because a central leaderboard
reads as authoritative.

So every row records **who adjudicated it**:

- `local` — a room on this node (Host, or a friend's node over the fabric). An
  honest personal record. Nothing comparative may ever be built on it.
- `server` — a room the game server ran, whose referee reported the result the
  way `games_server/hub.py` does for every other game. This is the only kind that
  can carry a rating.

The column exists *before* server-hosted matches do, on purpose. Without it, the
day a leaderboard appears is the day it silently ingests self-computed rows, and
there is no way to tell them apart afterwards.

### What is deliberately *not* here

**Rating.** The ladder lives on the game server (`games_server/store.py`:
`ratings`, real ELO, real tiers, per-account and per-game) and this node has no
authority over it. The old card printed `1520 + random.randint(18, 32)` and a
tier name to match, which looked exactly like a ladder and was not one. Rating
comes back with server-hosted matches, where the referee — not the player's
machine — decides what happened.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir

logger = logging.getLogger(__name__)

#: XP for turning up. A match played is worth something even when it went badly —
#: the alternative is a progression that only moves for people who are already
#: winning.
XP_BASE = 100
XP_PER_KILL = 25
XP_PER_HEAD_KILL = 15
XP_WIN = 250
#: Per hundred points of damage, so a support-shaped match is worth something
#: even when somebody else took the kills.
XP_PER_100_DAMAGE = 10

#: XP for level *n* → *n+1*. Flat rather than a curve: a curve is a balance
#: decision, and there is nothing yet for levels to gate.
XP_PER_LEVEL = 2000


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


def init_results_db() -> None:
    """Create the table (idempotent), mirroring the other module stores."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hassault_matches (
                id           TEXT PRIMARY KEY,
                account_id   TEXT NOT NULL,
                player_name  TEXT NOT NULL DEFAULT '',
                map_name     TEXT NOT NULL,
                room         TEXT NOT NULL DEFAULT '',
                kills        INTEGER NOT NULL DEFAULT 0,
                deaths       INTEGER NOT NULL DEFAULT 0,
                head_kills   INTEGER NOT NULL DEFAULT 0,
                damage_dealt INTEGER NOT NULL DEFAULT 0,
                opponents    INTEGER NOT NULL DEFAULT 0,
                won          INTEGER NOT NULL DEFAULT 0,
                mvp          INTEGER NOT NULL DEFAULT 0,
                xp           INTEGER NOT NULL DEFAULT 0,
                -- Who adjudicated this match: `local` (a room on this node) or
                -- `server` (the game server's referee). See the module docstring
                -- — this is the difference between a personal record and a
                -- result that anything comparative may be built on.
                authority    TEXT NOT NULL DEFAULT 'local',
                drop_id      TEXT,
                played_at    REAL NOT NULL,
                -- Set when the card has been shown and closed. A column rather
                -- than a delete: the row *is* the match history, and dismissing a
                -- card is not "that match did not happen".
                dismissed_at REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hassault_matches_account "
            "ON hassault_matches(account_id, played_at DESC)"
        )
        # **`CREATE TABLE IF NOT EXISTS` never adds a column to a table that
        # already exists.** An install that recorded a match before a column was
        # introduced keeps a table without it, and every read of that column then
        # fails — on exactly the machines that have been playing longest.
        #
        # Defaulting `authority` to `local` is also the honest backfill: those
        # matches were adjudicated by the node that wrote them. `drop_id` is
        # nullable because a match genuinely may not have earned one.
        _ensure_column(conn, "authority", "TEXT NOT NULL DEFAULT 'local'")
        _ensure_column(conn, "drop_id", "TEXT")


def _ensure_column(conn: sqlite3.Connection, name: str, decl: str) -> None:
    """Add one column to `hassault_matches` if this install predates it."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(hassault_matches)")}
    if name not in columns:
        conn.execute(f"ALTER TABLE hassault_matches ADD COLUMN {name} {decl}")


def xp_for(result: dict[str, Any]) -> int:
    """XP from what actually happened. Every term is a number the match produced."""
    kills = int(result.get("kills", 0))
    return (
        XP_BASE
        + kills * XP_PER_KILL
        + int(result.get("headKills", 0)) * XP_PER_HEAD_KILL
        + (XP_WIN if result.get("won") else 0)
        + int(result.get("damageDealt", 0)) // 100 * XP_PER_100_DAMAGE
    )


def record(
    account_id: str,
    result: dict[str, Any],
    drop_id: str | None = None,
    authority: str = "local",
) -> str:
    """Write one finished match. Returns its id.

    `authority` defaults to `local` because that is what a caller on this node
    can honestly claim. Only the path that receives a result *from the game
    server* passes `server`, and it does so having been told by the server rather
    than having computed anything.
    """
    init_results_db()
    match_id = uuid.uuid4().hex[:16]
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO hassault_matches
                (id, account_id, player_name, map_name, room, kills, deaths,
                 head_kills, damage_dealt, opponents, won, mvp, xp, authority,
                 drop_id, played_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                account_id,
                str(result.get("name", "")),
                str(result.get("map", "")),
                str(result.get("room", "")),
                int(result.get("kills", 0)),
                int(result.get("deaths", 0)),
                int(result.get("headKills", 0)),
                int(result.get("damageDealt", 0)),
                int(result.get("opponents", 0)),
                1 if result.get("won") else 0,
                1 if result.get("mvp") else 0,
                xp_for(result),
                "server" if authority == "server" else "local",
                drop_id,
                float(result.get("playedAt", time.time())),
            ),
        )
    return match_id


def attach_drop(match_id: str, instance_id: str) -> None:
    """Hang a rolled skin drop on the match that earned it.

    The drop and the row are written by two different things at two different
    times, which is why this exists at all. `record` runs when the player leaves
    the match, from the simulation's own counters; the drop is the reward for
    *finishing*, so it is rolled by `_watchdog_game_process` once the client
    process has actually exited and its result has landed. By then the row is
    already written, so the drop arrives as an update.

    Only the **id** is stored. The card wants the skin's name, rarity colour and
    wear, and copying those onto the match row would mean a renamed skin showing
    its old name forever; `GET /match/latest_summary` resolves the id against the
    inventory instead.

    A match id that matches nothing is reported rather than swallowed. That is
    the exact shape of the bug this function was missing for: the drop was rolled
    into the player's inventory, the call to attach it raised, and the card
    showed no drop — with the skin sitting in the armoury having come from
    nowhere.
    """
    init_results_db()
    with get_db_conn() as conn:
        cur = conn.execute(
            "UPDATE hassault_matches SET drop_id = ? WHERE id = ?",
            (instance_id, match_id),
        )
        if cur.rowcount == 0:
            logger.warning(
                "hassault: drop %s belongs to no match (%s); it is in the "
                "inventory but no card will show it",
                instance_id,
                match_id,
            )


def progression(account_id: str) -> dict[str, int]:
    """Total XP, level, and progress into it — **summed from the matches**.

    Derived rather than stored, so it cannot disagree with the history it is
    supposedly a summary of.
    """
    init_results_db()
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(xp), 0) AS total FROM hassault_matches WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    total = int(row["total"]) if row else 0
    return {
        "totalXp": total,
        # Level 1 is where everybody starts; level 0 would mean a player who has
        # never played is below somebody who played once and learned nothing.
        "level": total // XP_PER_LEVEL + 1,
        "levelProgressPercent": round((total % XP_PER_LEVEL) / XP_PER_LEVEL * 100),
    }


def latest(account_id: str) -> dict[str, Any] | None:
    """The most recent undismissed match, as the debrief card wants it."""
    init_results_db()
    with get_db_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM hassault_matches
            WHERE account_id = ? AND dismissed_at IS NULL
            ORDER BY played_at DESC LIMIT 1
            """,
            (account_id,),
        ).fetchone()
    if row is None:
        return None
    return to_summary(dict(row), progression(account_id))


def to_summary(row: dict[str, Any], level: dict[str, int]) -> dict[str, Any]:
    """One row plus the running total, in the shape the card reads."""
    kills = int(row["kills"])
    heads = int(row["head_kills"])
    return {
        "matchId": row["id"],
        "mapName": row["map_name"],
        "won": bool(row["won"]),
        "kills": kills,
        "deaths": int(row["deaths"]),
        "headshots": heads,
        # Guarded because a match with no kills has no denominator, not because
        # the division is unlikely — a card is shown for those matches too.
        "headshotPercent": round(heads / kills * 100, 1) if kills else 0.0,
        "damageDealt": int(row["damage_dealt"]),
        "opponents": int(row["opponents"]),
        "isMvp": bool(row["mvp"]),
        "xpGained": int(row["xp"]),
        # `local` or `server`. The card says so in as many words: a score this
        # machine kept for itself is a different claim from one a referee made.
        "authority": str(row["authority"]),
        "rated": str(row["authority"]) == "server",
        "currentLevel": level["level"],
        "levelProgressPercent": level["levelProgressPercent"],
        "totalXp": level["totalXp"],
        "earnedDrop": None,
        "dropId": row["drop_id"],
        "timestamp": float(row["played_at"]),
    }


def dismiss(account_id: str) -> None:
    """Mark every outstanding card for this account as seen."""
    init_results_db()
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE hassault_matches SET dismissed_at = ? "
            "WHERE account_id = ? AND dismissed_at IS NULL",
            (time.time(), account_id),
        )


def history(account_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Recent matches, newest first. The card shows one; this is the rest."""
    init_results_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM hassault_matches
            WHERE account_id = ? ORDER BY played_at DESC LIMIT ?
            """,
            (account_id, max(1, min(limit, 200))),
        ).fetchall()
    level = progression(account_id)
    return [to_summary(dict(r), level) for r in rows]
