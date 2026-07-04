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
        if len(seats) != 2:
            return  # ELO is 2-player for now; multi-player rating is a later phase.
        a, b = seats[0], seats[1]
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
