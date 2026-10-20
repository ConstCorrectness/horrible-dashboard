"""hassault's rating: real Glicko-2, between two players, on this server.

The node used to keep a rating of its own, moved by its own report of a
deathmatch scored against a fixed 1500 opponent: a number about one player and
nobody. This is the replacement, and it is only ever written here:

- **From a duel this server ran** (`modes/duel.py`): two seats, a winner, a
  draw, or a forfeit, reported once through the room's `on_over` hook.
- **Both players move together**, each against the other's *pre-match* rating
  (`rating.compute_glicko2_step`, the node's Glicko-2 step, reused).
- **Exactly once per match**: the `hassault_rated_matches` row goes in first and
  its primary key is the guard (migration `_m14_hassault_glicko`).
- **Signed-in accounts only.** The duel queue refuses guests, and a seat that
  is not an account is never rated.

Public reads under `/api/hassault/rating/*` (a rating is shown to opponents
anyway); the caller's own under `/api/hassault/account/rating`.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Header

from backend.games_server import store

router = APIRouter(prefix="/api/hassault", tags=["hassault-rating"])

DUEL = "duel"
QUEUES = (DUEL,)


def _defaults() -> tuple[float, float, float]:
    from backend.modules.hassault import rating

    return rating.DEFAULT_RATING, rating.DEFAULT_RD, rating.DEFAULT_VOL


def _row(conn: sqlite3.Connection, account_id: str, queue: str) -> dict[str, Any]:
    found = conn.execute(
        "SELECT * FROM hassault_glicko WHERE account_id = ? AND queue = ?",
        (account_id, queue),
    ).fetchone()
    if found is None:
        r, rd, vol = _defaults()
        return {
            "rating": r,
            "rd": rd,
            "vol": vol,
            "games": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
        }
    return dict(found)


def _out(account_id: str, queue: str, row: dict[str, Any]) -> dict[str, Any]:
    from backend.modules.hassault import rating, ranked_mmr

    tier, number = rating.tier_for_rating(float(row["rating"]))
    placed = int(row["games"]) >= ranked_mmr.PLACEMENT_MATCHES_REQUIRED
    return {
        "accountId": account_id,
        "queue": queue,
        "rating": round(float(row["rating"]), 1),
        "rd": round(float(row["rd"]), 1),
        "games": int(row["games"]),
        "wins": int(row["wins"]),
        "losses": int(row["losses"]),
        "draws": int(row["draws"]),
        # Shown once placed, as a tier with the number behind it; before then a
        # rating with an RD of 350 is a guess with a decimal point.
        "placed": placed,
        "placementGames": ranked_mmr.PLACEMENT_MATCHES_REQUIRED,
        "tier": tier if placed else "Unranked",
        "tierNumber": number if placed else 0,
        # The names the node's own ranked routes always used, so a client
        # reading the node cannot tell this rating came from the server.
        "tierName": tier if placed else "Unranked",
        "matchesPlayed": int(row["games"]),
        "isCalibrated": placed,
    }


def rating_of(account_id: str, queue: str = DUEL) -> dict[str, Any]:
    store.init_db()
    with store.get_conn() as conn:
        return _out(account_id, queue, _row(conn, account_id, queue))


def rate_match(
    match_id: str,
    a_id: str,
    b_id: str,
    score_a: float,
    queue: str = DUEL,
    reason: str = "",
) -> dict[str, Any] | None:
    """Rate one finished match between `a_id` and `b_id`.

    `score_a` is 1 for an A win, 0 for a B win, 0.5 for a draw. Returns both
    players' before and after, or `None` if this match was already rated (or is
    not a match between two different accounts).
    """
    from backend.modules.hassault import rating

    if not a_id or not b_id or a_id == b_id:
        return None
    score_a = min(1.0, max(0.0, float(score_a)))
    store.init_db()
    with store.get_conn() as conn:
        a = _row(conn, a_id, queue)
        b = _row(conn, b_id, queue)
        a_new = rating.compute_glicko2_step(
            a["rating"], a["rd"], a["vol"], b["rating"], b["rd"], score_a
        )
        b_new = rating.compute_glicko2_step(
            b["rating"], b["rd"], b["vol"], a["rating"], a["rd"], 1.0 - score_a
        )
        now = time.time()
        try:
            # The ledger row first: its primary key is what makes a second
            # report of the same match a no-op rather than a second rating.
            conn.execute(
                "INSERT INTO hassault_rated_matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    match_id,
                    queue,
                    a_id,
                    b_id,
                    score_a,
                    a["rating"],
                    a_new[0],
                    b["rating"],
                    b_new[0],
                    reason,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            return None
        for account, before, after, score in (
            (a_id, a, a_new, score_a),
            (b_id, b, b_new, 1.0 - score_a),
        ):
            conn.execute(
                "INSERT INTO hassault_glicko VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?) "
                "ON CONFLICT(account_id, queue) DO UPDATE SET rating = excluded.rating, "
                "rd = excluded.rd, vol = excluded.vol, games = games + 1, "
                "wins = wins + excluded.wins, losses = losses + excluded.losses, "
                "draws = draws + excluded.draws, updated_at = excluded.updated_at",
                (
                    account,
                    queue,
                    after[0],
                    after[1],
                    after[2],
                    1 if score > 0.5 else 0,
                    1 if score < 0.5 else 0,
                    1 if score == 0.5 else 0,
                    now,
                ),
            )
    return {
        "matchId": match_id,
        "queue": queue,
        a_id: {"before": round(a["rating"], 1), "after": round(a_new[0], 1)},
        b_id: {"before": round(b["rating"], 1), "after": round(b_new[0], 1)},
    }


def leaderboard(queue: str = DUEL, limit: int = 50) -> list[dict[str, Any]]:
    from backend.modules.hassault import ranked_mmr

    store.init_db()
    with store.get_conn() as conn:
        rows = conn.execute(
            "SELECT g.*, a.display_name AS name FROM hassault_glicko g "
            "LEFT JOIN accounts a ON a.id = g.account_id "
            "WHERE g.queue = ? AND g.games >= ? ORDER BY g.rating DESC LIMIT ?",
            (queue, ranked_mmr.PLACEMENT_MATCHES_REQUIRED, max(1, min(200, limit))),
        ).fetchall()
    out = []
    for rank, row in enumerate(rows, start=1):
        entry = _out(str(row["account_id"]), queue, dict(row))
        entry["rank"] = rank
        entry["name"] = row["name"] or entry["accountId"]
        out.append(entry)
    return out


# ---- routes ---------------------------------------------------------------------


@router.get("/rating/leaderboard")
def get_leaderboard(queue: str = DUEL, limit: int = 50) -> list[dict[str, Any]]:
    return leaderboard(queue if queue in QUEUES else DUEL, limit)


@router.get("/rating/{account_id}")
def get_rating(account_id: str, queue: str = DUEL) -> dict[str, Any]:
    return rating_of(account_id, queue if queue in QUEUES else DUEL)


@router.get("/account/rating")
def get_my_rating(
    queue: str = DUEL, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    from backend.games_server.hassault_account import _account

    return rating_of(_account(authorization), queue if queue in QUEUES else DUEL)
