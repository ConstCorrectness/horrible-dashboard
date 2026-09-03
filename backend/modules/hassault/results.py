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
from collections.abc import Mapping
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

#: Per bomb planted or defused, per flag captured.
#:
#: Deliberately worth six kills. It is not generosity — it is the same argument
#: `XP_PER_100_DAMAGE` makes, taken to the mode that needs it most: in defuse the
#: player who plants under fire and dies for it decided the round, and paying
#: them by their kill count describes a different game than the one the scoreline
#: showed. `outcome_for` already weights an objective at three kills for MVP; XP
#: weights it higher because MVP is a comparison within a team and this is not.
XP_PER_OBJECTIVE = 150

#: Per round their side took.
#:
#: Small on purpose. Winning the match already pays `XP_WIN`, and a large
#: per-round term would make a 5-4 grind worth more than a 5-0 — which would be
#: rewarding length rather than play. It exists so a side that loses 4-5 is not
#: recorded as having done the same as one that lost 0-5.
XP_ROUND_WIN = 40

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
                -- What was being played. Recorded because it cannot be
                -- recovered afterwards: a 5-3 in rounds and a 5-3 in kills are
                -- the same two numbers, and only this column tells them apart.
                mode         TEXT NOT NULL DEFAULT 'dm',
                kills        INTEGER NOT NULL DEFAULT 0,
                deaths       INTEGER NOT NULL DEFAULT 0,
                head_kills   INTEGER NOT NULL DEFAULT 0,
                damage_dealt INTEGER NOT NULL DEFAULT 0,
                -- Bombs planted or defused, flags captured. Stored rather than
                -- folded into `xp`, or the card can show what it was paid for
                -- but not what earned it.
                objectives   INTEGER NOT NULL DEFAULT 0,
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
        # `dm` is the honest backfill rather than a placeholder: before modes
        # existed deathmatch was the only thing a room could be, so every row
        # written without this column genuinely was one. `objectives` backfills
        # to zero for the same reason — those modes had none to score.
        _ensure_column(conn, "mode", "TEXT NOT NULL DEFAULT 'dm'")
        _ensure_column(conn, "objectives", "INTEGER NOT NULL DEFAULT 0")


def _ensure_column(conn: sqlite3.Connection, name: str, decl: str) -> None:
    """Add one column to `hassault_matches` if this install predates it."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(hassault_matches)")}
    if name not in columns:
        conn.execute(f"ALTER TABLE hassault_matches ADD COLUMN {name} {decl}")


def is_recordable(result: Mapping[str, Any]) -> bool:
    """Whether a finished match is worth writing down at all.

    The post-match card used to appear for sessions nobody would call a match:
    open the pane, deploy, press Menu, and a row was written — a row that then
    read as a **VICTORY**, because `result_for` scored a lone player against
    `max(others, default=-1)` and `0 >= -1`. The card became something that
    happened *to* you rather than something you earned.

    Two conditions, and both are about the match rather than about how well it
    went:

    - **Somebody to play against.** A solo session on a map has no result. Bots
      count — losing to one is losing, the same argument `result_for` makes about
      `won`.
    - **Something happened.** A kill, a death, damage dealt — **or an objective**.
      A round you spent walking is not a round.

    That last term is not a nicety. The first three describe a *fight*, and in
    defuse the fight is not the game: a player who planted the bomb twice, was
    traded for both times by somebody else's shot and never landed one of their
    own has `kills == deaths == damageDealt == 0`. Without it they file as "not a
    match", get no card and no XP, and the mode most likely to produce that
    player is the one this predicate was never updated for.

    Deliberately *not* a quality bar: a match where you were flattened 0-15 is
    recordable, and it should be. What is being excluded is the *empty* session,
    not the bad one — XP has a `XP_BASE` term precisely so turning up counts.

    Takes a plain mapping rather than a `MatchPlayer` so the same three numbers
    can be replayed from a fixture by the backend suite and by the native
    client's own conformance tests, which have no `MatchRoom` to build.
    """
    if int(result.get("opponents", 0)) <= 0:
        return False
    return (
        int(result.get("kills", 0)) > 0
        or int(result.get("deaths", 0)) > 0
        or int(result.get("damageDealt", 0)) > 0
        or int(result.get("objectives", 0)) > 0
    )


def xp_for(result: dict[str, Any]) -> int:
    """XP from what actually happened. Every term is a number the match produced."""
    kills = int(result.get("kills", 0))
    return (
        XP_BASE
        + kills * XP_PER_KILL
        + int(result.get("headKills", 0)) * XP_PER_HEAD_KILL
        + (XP_WIN if result.get("won") else 0)
        + int(result.get("damageDealt", 0)) // 100 * XP_PER_100_DAMAGE
        + int(result.get("objectives", 0)) * XP_PER_OBJECTIVE
        + int(result.get("roundsWon", 0)) * XP_ROUND_WIN
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
                (id, account_id, player_name, map_name, room, mode, kills,
                 deaths, head_kills, damage_dealt, objectives, opponents, won,
                 mvp, xp, authority, drop_id, played_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                account_id,
                str(result.get("name", "")),
                str(result.get("map", "")),
                str(result.get("room", "")),
                str(result.get("mode") or "dm"),
                int(result.get("kills", 0)),
                int(result.get("deaths", 0)),
                int(result.get("headKills", 0)),
                int(result.get("damageDealt", 0)),
                int(result.get("objectives", 0)),
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


def _int(row: Any, key: str, default: int = 0) -> int:
    """One integer column, tolerating a mapping that predates it.

    `_ensure_column` backfills the table itself, so a real row always has these.
    What does not is a **hand-built mapping** — the fixtures and the odd caller
    that assembles a row to render one card — and a `KeyError` there would turn a
    missing column into a 500 on the debrief.
    """
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else int(value)


def _str(row: Any, key: str, default: str = "") -> str:
    """One text column, on the same terms as [`_int`]."""
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else str(value)


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
        # Read with the same `_row` guard the rest of this uses: a row selected
        # from a table that predates a column has the column (SQLite backfills on
        # `ALTER`), but a caller replaying a hand-built dict in a test may not.
        "objectives": _int(row, "objectives"),
        "mode": _str(row, "mode", "dm"),
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
