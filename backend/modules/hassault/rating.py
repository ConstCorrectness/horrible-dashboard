"""Ranked Competitive Elo & Glicko-2 Matchmaking Rating (MMR) Engine.

Computes skill ratings, rating deviation (uncertainty), and volatility with
multi-factor individual performance scaling (ADR, HS%, First Bloods, Nutshot Kills, Round Differential).
"""

from __future__ import annotations

import math
import sqlite3
import time
from typing import Any
from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.hassault.results import get_db_conn

# Glicko-2 System Constants
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOL = 0.06
TAU = 0.5  # System constant constraining volatility change over time
GLICKO2_SCALE = 173.7178

TIER_THRESHOLDS = [
    (2400.0, "The Global Elite", 18),
    (2200.0, "Supreme Master First Class", 17),
    (2000.0, "Legendary Eagle Master", 16),
    (1850.0, "Legendary Eagle", 15),
    (1700.0, "Distinguished Master Guardian", 14),
    (1600.0, "Master Guardian Elite", 13),
    (1500.0, "Master Guardian II", 12),
    (1400.0, "Master Guardian I", 11),
    (1300.0, "Gold Nova Master", 10),
    (1200.0, "Gold Nova III", 9),
    (1100.0, "Gold Nova II", 8),
    (1000.0, "Gold Nova I", 7),
    (900.0, "Silver Elite Master", 6),
    (800.0, "Silver Elite", 5),
    (700.0, "Silver IV", 4),
    (600.0, "Silver III", 3),
    (500.0, "Silver II", 2),
    (0.0, "Silver I", 1),
]


def tier_for_rating(rating: float) -> tuple[str, int]:
    """Returns (tier_name, tier_number) for a given numeric rating."""
    for threshold, name, num in TIER_THRESHOLDS:
        if rating >= threshold:
            return name, num
    return "Silver I", 1


def init_rating_db() -> None:
    """Initialize the hassault_ratings table in app.db (idempotent)."""
    from backend.modules.hassault.results import init_results_db
    init_results_db()
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hassault_ratings (
                account_id     TEXT PRIMARY KEY,
                rating         REAL NOT NULL DEFAULT 1500.0,
                rd             REAL NOT NULL DEFAULT 350.0,
                vol            REAL NOT NULL DEFAULT 0.06,
                tier           TEXT NOT NULL DEFAULT 'Master Guardian II',
                tier_number    INTEGER NOT NULL DEFAULT 12,
                matches_played INTEGER NOT NULL DEFAULT 0,
                wins           INTEGER NOT NULL DEFAULT 0,
                losses         INTEGER NOT NULL DEFAULT 0,
                last_change    REAL NOT NULL DEFAULT 0.0,
                updated_at     REAL NOT NULL DEFAULT 0.0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hassault_ratings_leaderboard "
            "ON hassault_ratings(rating DESC)"
        )


def _to_glicko2(rating: float, rd: float) -> tuple[float, float]:
    """Convert standard scale to Glicko-2 internal scale."""
    mu = (rating - DEFAULT_RATING) / GLICKO2_SCALE
    phi = rd / GLICKO2_SCALE
    return mu, phi


def _from_glicko2(mu: float, phi: float) -> tuple[float, float]:
    """Convert Glicko-2 internal scale back to standard display scale."""
    rating = mu * GLICKO2_SCALE + DEFAULT_RATING
    rd = phi * GLICKO2_SCALE
    return max(100.0, min(4000.0, rating)), max(30.0, min(350.0, rd))


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * (phi**2) / (math.pi**2))


def _E(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def compute_glicko2_step(
    rating: float,
    rd: float,
    vol: float,
    opp_rating: float,
    opp_rd: float,
    actual_score: float,
) -> tuple[float, float, float]:
    """Calculate new rating, RD, and volatility using standard Glicko-2 algorithm."""
    mu, phi = _to_glicko2(rating, rd)
    mu_j, phi_j = _to_glicko2(opp_rating, opp_rd)

    g_j = _g(phi_j)
    e_j = _E(mu, mu_j, phi_j)

    v = 1.0 / ((g_j**2) * e_j * (1.0 - e_j))
    delta = v * g_j * (actual_score - e_j)

    a = math.log(vol**2)

    def f(x: float) -> float:
        ex = math.exp(x)
        d_sq = delta**2
        phi_sq = phi**2
        num1 = ex * (d_sq - phi_sq - v - ex)
        den1 = 2.0 * ((phi_sq + v + ex) ** 2)
        term1 = num1 / den1
        term2 = (x - a) / (TAU**2)
        return term1 - term2

    A = a
    if delta**2 > phi**2 + v:
        B = math.log(delta**2 - phi**2 - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU

    f_A = f(A)
    f_B = f(B)

    # Illinois algorithm bracket solver
    for _ in range(35):
        if abs(B - A) <= 1e-6:
            break
        C = A - f_A * (A - B) / (f_A - f_B)
        f_C = f(C)
        if f_C * f_B < 0:
            A = B
            f_A = f_B
        else:
            f_A = f_A / 2.0
        B = C
        f_B = f_C

    new_vol = math.exp(A / 2.0)
    phi_star = math.sqrt(phi**2 + new_vol**2)
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star**2) + 1.0 / v)
    new_mu = mu + (new_phi**2) * g_j * (actual_score - e_j)

    new_rating, new_rd = _from_glicko2(new_mu, new_phi)
    return new_rating, new_rd, new_vol


def calculate_performance_score(result: dict[str, Any]) -> float:
    """Combines match outcome with round differential and individual combat impact.

    Returns an adjusted performance score in [0.0, 1.0].
    """
    won = bool(result.get("won", False))
    rounds_won = int(result.get("roundsWon", 0))
    rounds_lost = int(result.get("roundsLost", 0))
    total_rounds = max(1, rounds_won + rounds_lost)

    # Base match result score (0.0 to 1.0)
    base_score = 1.0 if won else 0.0
    if rounds_won + rounds_lost > 0:
        # Round ratio adjustment (e.g. 13-1 win gives stronger boost than 13-11)
        round_factor = rounds_won / total_rounds
        base_score = 0.6 * base_score + 0.4 * round_factor

    # Individual impact bonuses
    adr = float(result.get("adr", 0.0))
    first_bloods = int(result.get("firstBloods", 0))
    nutshot_kills = int(result.get("nutshotKills", 0))
    hs_rate = float(result.get("hsRate", 0.0))
    mvp = 1.0 if result.get("mvp") else 0.0

    # Impact modifier in range [-0.15, +0.15]
    impact_mod = 0.0
    if adr > 100.0:
        impact_mod += min(0.08, (adr - 100.0) / 1000.0)
    elif adr < 50.0:
        impact_mod -= min(0.06, (50.0 - adr) / 1000.0)

    impact_mod += min(0.04, first_bloods * 0.015)
    impact_mod += min(0.02, nutshot_kills * 0.01)
    impact_mod += min(0.03, hs_rate * 0.03)
    impact_mod += 0.02 if mvp else 0.0

    return max(0.02, min(0.98, base_score + impact_mod))


def get_player_rating(account_id: str) -> dict[str, Any]:
    """Retrieve current Glicko-2 rating record for account_id."""
    init_rating_db()
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM hassault_ratings WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row:
            d = dict(row)
            d["tierName"] = d["tier"]
            d["tierNumber"] = d["tier_number"]
            d["matchesPlayed"] = d["matches_played"]
            d["lastChange"] = d["last_change"]
            d["updatedAt"] = d["updated_at"]
            return d

        tier_name, tier_num = tier_for_rating(DEFAULT_RATING)
        return {
            "account_id": account_id,
            "rating": DEFAULT_RATING,
            "rd": DEFAULT_RD,
            "vol": DEFAULT_VOL,
            "tier": tier_name,
            "tierName": tier_name,
            "tierNumber": tier_num,
            "matchesPlayed": 0,
            "wins": 0,
            "losses": 0,
            "lastChange": 0.0,
            "updatedAt": 0.0,
        }


def update_player_rating(
    account_id: str,
    result: dict[str, Any],
    opp_rating: float = DEFAULT_RATING,
    opp_rd: float = 200.0,
) -> dict[str, Any]:
    """Update rating based on match outcome and individual impact."""
    init_rating_db()
    current = get_player_rating(account_id)
    actual_score = calculate_performance_score(result)

    new_rating, new_rd, new_vol = compute_glicko2_step(
        current["rating"],
        current["rd"],
        current["vol"],
        opp_rating,
        opp_rd,
        actual_score,
    )

    tier_name, tier_num = tier_for_rating(new_rating)
    delta = new_rating - current["rating"]
    won = bool(result.get("won", False))
    wins = current["wins"] + (1 if won else 0)
    losses = current["losses"] + (0 if won else 1)
    now = time.time()

    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO hassault_ratings
                (account_id, rating, rd, vol, tier, tier_number, matches_played,
                 wins, losses, last_change, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                rating = excluded.rating,
                rd = excluded.rd,
                vol = excluded.vol,
                tier = excluded.tier,
                tier_number = excluded.tier_number,
                matches_played = matches_played + 1,
                wins = excluded.wins,
                losses = excluded.losses,
                last_change = excluded.last_change,
                updated_at = excluded.updated_at
            """,
            (
                account_id,
                round(new_rating, 1),
                round(new_rd, 1),
                round(new_vol, 4),
                tier_name,
                tier_num,
                current["matchesPlayed"] + 1,
                wins,
                losses,
                round(delta, 1),
                now,
            ),
        )

    return {
        "account_id": account_id,
        "rating": round(new_rating, 1),
        "rd": round(new_rd, 1),
        "vol": round(new_vol, 4),
        "tier": tier_name,
        "tierName": tier_name,
        "tierNumber": tier_num,
        "matchesPlayed": current["matchesPlayed"] + 1,
        "wins": wins,
        "losses": losses,
        "delta": round(delta, 1),
        "lastChange": round(delta, 1),
        "updatedAt": now,
    }


def get_leaderboard(limit: int = 50) -> list[dict[str, Any]]:
    """Return top rated competitive players."""
    init_rating_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.*, m.player_name
            FROM hassault_ratings r
            LEFT JOIN (
                SELECT account_id, player_name, MAX(played_at)
                FROM hassault_matches
                GROUP BY account_id
            ) m ON r.account_id = m.account_id
            WHERE r.matches_played > 0
            ORDER BY r.rating DESC
            LIMIT ?
            """,
            (max(1, min(100, limit)),),
        ).fetchall()
        out = []
        for idx, row in enumerate(rows, start=1):
            d = dict(row)
            d["rank"] = idx
            d["name"] = d.get("player_name") or d["account_id"][:8]
            out.append(d)
        return out
