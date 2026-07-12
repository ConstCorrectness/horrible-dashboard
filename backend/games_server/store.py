"""Persistent accounts + the ELO ladder for the game server.

SQLite at `$HORRIBLE_DATA_DIR/game_server.db` (separate from a node's own data). Three
tables: **accounts** (who played), **ratings** (per-account, per-game ELO + W/L/D), and
**results** (a log of finished games). The referee calls `record_result` on `game_over`;
the leaderboard reads `ratings`.

Ratings are 2-player zero-sum ELO for now (tic-tac-toe/chess/etc.); multi-player games
(poker) will need a pairwise or placement-based extension.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

BASE_RATING = 1200.0
ELO_K = 32.0


def get_db_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "game_server.db"


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id           TEXT PRIMARY KEY,
                provider     TEXT NOT NULL,
                subject      TEXT NOT NULL,
                display_name TEXT NOT NULL,
                created_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ratings (
                account_id TEXT NOT NULL,
                game_id    TEXT NOT NULL,
                rating     REAL NOT NULL,
                wins       INTEGER NOT NULL DEFAULT 0,
                losses     INTEGER NOT NULL DEFAULT 0,
                draws      INTEGER NOT NULL DEFAULT 0,
                games      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (account_id, game_id)
            );
            CREATE TABLE IF NOT EXISTS results (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id    TEXT NOT NULL,
                table_id   TEXT NOT NULL,
                created_at REAL NOT NULL,
                winner     INTEGER,
                payload    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS challenge_scores (
                account_id TEXT NOT NULL,
                game_id    TEXT NOT NULL,
                correct    INTEGER NOT NULL,
                total      INTEGER NOT NULL,
                score      REAL NOT NULL,
                categories TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (account_id, game_id)
            );
            -- The Plaza (human social layer): gamified profiles + friendships.
            CREATE TABLE IF NOT EXISTS player_profiles (
                account_id TEXT PRIMARY KEY,
                avatar     TEXT NOT NULL DEFAULT '🙂',
                bio        TEXT NOT NULL DEFAULT '',
                xp         INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            -- One row per friendship, stored canonically (account_a < account_b).
            -- `requested_by` is the side that asked; `status` is pending|accepted.
            CREATE TABLE IF NOT EXISTS friendships (
                account_a    TEXT NOT NULL,
                account_b    TEXT NOT NULL,
                status       TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                updated_at   REAL NOT NULL,
                PRIMARY KEY (account_a, account_b)
            );
            """
        )


# ---- accounts --------------------------------------------------------------


def upsert_account(provider: str, subject: str, display_name: str) -> str:
    """Create or refresh an account; returns its stable id (`provider:subject`)."""
    init_db()
    account_id = f"{provider}:{subject}"
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO accounts (id, provider, subject, display_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name
            """,
            (account_id, provider, subject, display_name, time.time()),
        )
    return account_id


def get_account(account_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
    return dict(row) if row else None


# ---- ratings ---------------------------------------------------------------


def _get_rating_row(
    conn: sqlite3.Connection, account_id: str, game_id: str
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM ratings WHERE account_id = ? AND game_id = ?",
        (account_id, game_id),
    ).fetchone()
    if row:
        return dict(row)
    return {
        "account_id": account_id,
        "game_id": game_id,
        "rating": BASE_RATING,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "games": 0,
    }


def _write_rating(conn: sqlite3.Connection, r: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO ratings (account_id, game_id, rating, wins, losses, draws, games)
        VALUES (:account_id, :game_id, :rating, :wins, :losses, :draws, :games)
        ON CONFLICT(account_id, game_id) DO UPDATE SET
            rating = excluded.rating, wins = excluded.wins,
            losses = excluded.losses, draws = excluded.draws, games = excluded.games
        """,
        r,
    )


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def _score(payoff: float) -> float:
    """ELO actual-score from a zero-sum payoff: win=1, draw=0.5, loss=0."""
    return 1.0 if payoff > 0 else 0.0 if payoff < 0 else 0.5


def record_result(
    game_id: str,
    table_id: str,
    seats: list[str],
    returns: dict[int, float],
    winner: int | None,
) -> None:
    """Persist a finished game and update the two seats' ELO ratings."""
    init_db()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO results (game_id, table_id, created_at, winner, payload) VALUES (?, ?, ?, ?, ?)",
            (
                game_id,
                table_id,
                time.time(),
                winner,
                json.dumps({"seats": seats, "returns": returns}),
            ),
        )
        # Everyone who played earns XP toward their Plaza level (win 20 / draw 10 /
        # loss 5), so the social profile grows just by showing up and competing.
        for idx, account_id in enumerate(seats):
            payoff = returns.get(idx, 0.0)
            gained = 20 if payoff > 0 else 5 if payoff < 0 else 10
            _add_xp(conn, account_id, gained)
        if len(seats) != 2:
            return  # ELO is 2-player for now; multi-player rating is a later phase.
        a, b = seats[0], seats[1]
        if a == b:
            return  # same account on both seats (two devices self-playing): don't
            # rate an account against itself — the result log above still captures it.
        ra = _get_rating_row(conn, a, game_id)
        rb = _get_rating_row(conn, b, game_id)
        sa = _score(returns.get(0, 0.0))
        sb = _score(returns.get(1, 0.0))
        ea = _expected(ra["rating"], rb["rating"])
        eb = _expected(rb["rating"], ra["rating"])
        ra["rating"] += ELO_K * (sa - ea)
        rb["rating"] += ELO_K * (sb - eb)
        for r, s in ((ra, sa), (rb, sb)):
            r["games"] += 1
            r["wins"] += 1 if s == 1.0 else 0
            r["losses"] += 1 if s == 0.0 else 0
            r["draws"] += 1 if s == 0.5 else 0
        _write_rating(conn, ra)
        _write_rating(conn, rb)


def record_challenge(account_id: str, game_id: str, report: dict[str, Any]) -> bool:
    """Persist a challenge attempt, keeping only the player's **best** (most correct)
    per game. Returns True if this attempt is a new best."""
    init_db()
    correct = int(report.get("correct", 0))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT correct FROM challenge_scores WHERE account_id = ? AND game_id = ?",
            (account_id, game_id),
        ).fetchone()
        if row is not None and row["correct"] >= correct:
            return False  # not an improvement — keep the existing best
        conn.execute(
            """
            INSERT INTO challenge_scores
                (account_id, game_id, correct, total, score, categories, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, game_id) DO UPDATE SET
                correct = excluded.correct, total = excluded.total,
                score = excluded.score, categories = excluded.categories,
                updated_at = excluded.updated_at
            """,
            (
                account_id,
                game_id,
                correct,
                int(report.get("total", 0)),
                float(report.get("score", 0.0)),
                json.dumps(report.get("categories", {})),
                time.time(),
            ),
        )
    return True


def challenge_leaderboard(game_id: str, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.account_id, c.correct, c.total, c.score,
                   COALESCE(a.display_name, c.account_id) AS display_name
            FROM challenge_scores c
            LEFT JOIN accounts a ON a.id = c.account_id
            WHERE c.game_id = ?
            ORDER BY c.correct DESC, c.updated_at ASC
            LIMIT ?
            """,
            (game_id, limit),
        ).fetchall()
    return [
        {
            "account_id": r["account_id"],
            "display_name": r["display_name"],
            "correct": r["correct"],
            "total": r["total"],
            "score": round(r["score"], 3),
        }
        for r in rows
    ]


def leaderboard(game_id: str, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.account_id, r.rating, r.wins, r.losses, r.draws, r.games,
                   COALESCE(a.display_name, r.account_id) AS display_name
            FROM ratings r
            LEFT JOIN accounts a ON a.id = r.account_id
            WHERE r.game_id = ?
            ORDER BY r.rating DESC
            LIMIT ?
            """,
            (game_id, limit),
        ).fetchall()
    return [
        {
            "account_id": row["account_id"],
            "display_name": row["display_name"],
            "rating": round(row["rating"], 1),
            "wins": row["wins"],
            "losses": row["losses"],
            "draws": row["draws"],
            "games": row["games"],
        }
        for row in rows
    ]


# ---- Plaza: gamified profiles ---------------------------------------------

# Cumulative XP thresholds; your level is the number of thresholds you've passed.
# The curve steepens so early levels come fast (sign-in + a couple of games) and
# later ones are a grind — the usual gamified shape.
LEVEL_THRESHOLDS = (0, 30, 90, 200, 400, 700, 1150, 1800, 2700, 4000, 6000)


def level_for_xp(xp: int) -> int:
    """1-based level for a total XP: level N once you've crossed threshold N-1."""
    level = 1
    for threshold in LEVEL_THRESHOLDS[1:]:
        if xp >= threshold:
            level += 1
        else:
            break
    return level


def _next_threshold(level: int) -> int | None:
    """XP needed to reach the next level (None once past the last threshold)."""
    return LEVEL_THRESHOLDS[level] if level < len(LEVEL_THRESHOLDS) else None


def _profile_view(account_id: str, avatar: str, bio: str, xp: int) -> dict[str, Any]:
    level = level_for_xp(xp)
    return {
        "account_id": account_id,
        "avatar": avatar,
        "bio": bio,
        "xp": xp,
        "level": level,
        "level_floor": LEVEL_THRESHOLDS[level - 1],
        "next_level_xp": _next_threshold(level),
    }


def get_profile(account_id: str) -> dict[str, Any]:
    """A player's gamified profile (avatar, bio, xp, derived level). Returns sane
    defaults for an account that has never touched the Plaza."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT avatar, bio, xp FROM player_profiles WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    if row is None:
        return _profile_view(account_id, "🙂", "", 0)
    return _profile_view(account_id, row["avatar"], row["bio"], row["xp"])


def upsert_profile(
    account_id: str, *, avatar: str | None = None, bio: str | None = None
) -> dict[str, Any]:
    """Create or patch a profile's avatar/bio (XP is only earned via `add_xp`)."""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT avatar, bio, xp FROM player_profiles WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        cur_avatar = row["avatar"] if row else "🙂"
        cur_bio = row["bio"] if row else ""
        cur_xp = row["xp"] if row else 0
        new_avatar = avatar if avatar is not None else cur_avatar
        new_bio = bio if bio is not None else cur_bio
        conn.execute(
            """
            INSERT INTO player_profiles (account_id, avatar, bio, xp, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                avatar = excluded.avatar, bio = excluded.bio,
                updated_at = excluded.updated_at
            """,
            (account_id, new_avatar, new_bio, cur_xp, time.time()),
        )
    return _profile_view(account_id, new_avatar, new_bio, cur_xp)


def _add_xp(conn: sqlite3.Connection, account_id: str, amount: int) -> None:
    """Grant XP inside an open transaction (used by `record_result`)."""
    conn.execute(
        """
        INSERT INTO player_profiles (account_id, avatar, bio, xp, updated_at)
        VALUES (?, '🙂', '', ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            xp = xp + excluded.xp, updated_at = excluded.updated_at
        """,
        (account_id, amount, time.time()),
    )


def add_xp(account_id: str, amount: int) -> dict[str, Any]:
    """Grant XP and return the updated profile."""
    init_db()
    with get_conn() as conn:
        _add_xp(conn, account_id, amount)
    return get_profile(account_id)


# ---- Plaza: friendships ----------------------------------------------------


def _pair(a: str, b: str) -> tuple[str, str]:
    """Canonical (account_a, account_b) ordering so each friendship is one row."""
    return (a, b) if a <= b else (b, a)


def request_friend(requester: str, target: str) -> str:
    """Ask to friend `target`. If they'd already asked you, this accepts (mutual
    intent → friends). Returns the resulting status ('pending' or 'accepted')."""
    if requester == target:
        return "self"
    init_db()
    a, b = _pair(requester, target)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, requested_by FROM friendships WHERE account_a = ? AND account_b = ?",
            (a, b),
        ).fetchone()
        if row is not None:
            if row["status"] == "accepted":
                return "accepted"
            # A pending request the *other* side opened → this reply accepts it.
            if row["requested_by"] != requester:
                conn.execute(
                    "UPDATE friendships SET status = 'accepted', updated_at = ? WHERE account_a = ? AND account_b = ?",
                    (time.time(), a, b),
                )
                return "accepted"
            return "pending"
        conn.execute(
            "INSERT INTO friendships (account_a, account_b, status, requested_by, updated_at) VALUES (?, ?, 'pending', ?, ?)",
            (a, b, requester, time.time()),
        )
    return "pending"


def accept_friend(account_id: str, other: str) -> bool:
    """Accept an incoming pending request. Only the addressee (not the requester)
    can accept. Returns True if a pending request became a friendship."""
    init_db()
    a, b = _pair(account_id, other)
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE friendships SET status = 'accepted', updated_at = ?
            WHERE account_a = ? AND account_b = ?
              AND status = 'pending' AND requested_by = ?
            """,
            (time.time(), a, b, other),
        )
        return cur.rowcount > 0


def remove_friend(account_id: str, other: str) -> None:
    """Remove a friend or decline/cancel a pending request (either side may)."""
    init_db()
    a, b = _pair(account_id, other)
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM friendships WHERE account_a = ? AND account_b = ?", (a, b)
        )


def _friend_row_view(conn: sqlite3.Connection, other_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COALESCE(a.display_name, ?) AS display_name,
               COALESCE(p.avatar, '🙂') AS avatar,
               COALESCE(p.xp, 0) AS xp
        FROM (SELECT ? AS id) x
        LEFT JOIN accounts a ON a.id = x.id
        LEFT JOIN player_profiles p ON p.account_id = x.id
        """,
        (other_id, other_id),
    ).fetchone()
    return {
        "account_id": other_id,
        "display_name": row["display_name"],
        "avatar": row["avatar"],
        "level": level_for_xp(row["xp"]),
    }


def list_friends(account_id: str) -> list[dict[str, Any]]:
    """Accepted friends of `account_id` (both directions), with avatar + level."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT CASE WHEN account_a = ? THEN account_b ELSE account_a END AS other
            FROM friendships
            WHERE (account_a = ? OR account_b = ?) AND status = 'accepted'
            """,
            (account_id, account_id, account_id),
        ).fetchall()
        return [_friend_row_view(conn, r["other"]) for r in rows]


def list_pending(account_id: str) -> list[dict[str, Any]]:
    """Incoming pending requests (someone else asked to friend `account_id`)."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT requested_by FROM friendships
            WHERE (account_a = ? OR account_b = ?)
              AND status = 'pending' AND requested_by != ?
            """,
            (account_id, account_id, account_id),
        ).fetchall()
        return [_friend_row_view(conn, r["requested_by"]) for r in rows]
