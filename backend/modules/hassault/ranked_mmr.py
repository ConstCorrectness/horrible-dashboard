"""Authoritative Ranked Matchmaking & Seasonal ELO/MMR Engine.

Provides competitive skill rating, dynamic K-factor scaling for calibration
placement matches, season progression, and ranked career profiles.
"""

from __future__ import annotations

import time
from typing import Any
from backend.modules.hassault.results import get_db_conn
from backend.modules.hassault import rating

CURRENT_SEASON = "Season 1: Global Championship"
PLACEMENT_MATCHES_REQUIRED = 10

TIER_DETAILS = {
    18: {"tier": "The Global Elite", "badge": "global_elite", "min_rating": 2400.0},
    17: {"tier": "Supreme Master First Class", "badge": "supreme", "min_rating": 2200.0},
    16: {"tier": "Legendary Eagle Master", "badge": "lem", "min_rating": 2000.0},
    15: {"tier": "Legendary Eagle", "badge": "le", "min_rating": 1850.0},
    14: {"tier": "Distinguished Master Guardian", "badge": "dmg", "min_rating": 1700.0},
    13: {"tier": "Master Guardian Elite", "badge": "mge", "min_rating": 1600.0},
    12: {"tier": "Master Guardian II", "badge": "mg2", "min_rating": 1500.0},
    11: {"tier": "Master Guardian I", "badge": "mg1", "min_rating": 1400.0},
    10: {"tier": "Gold Nova Master", "badge": "gnm", "min_rating": 1300.0},
    9: {"tier": "Gold Nova III", "badge": "gn3", "min_rating": 1200.0},
    8: {"tier": "Gold Nova II", "badge": "gn2", "min_rating": 1100.0},
    7: {"tier": "Gold Nova I", "badge": "gn1", "min_rating": 1000.0},
    6: {"tier": "Silver Elite Master", "badge": "sem", "min_rating": 900.0},
    5: {"tier": "Silver Elite", "badge": "se", "min_rating": 800.0},
    4: {"tier": "Silver IV", "badge": "s4", "min_rating": 700.0},
    3: {"tier": "Silver III", "badge": "s3", "min_rating": 600.0},
    2: {"tier": "Silver II", "badge": "s2", "min_rating": 500.0},
    1: {"tier": "Silver I", "badge": "s1", "min_rating": 0.0},
}


def init_ranked_seasons_db() -> None:
    """Initialize ranked seasons and match history tables (idempotent)."""
    rating.init_rating_db()
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hassault_ranked_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id     TEXT NOT NULL,
                match_id       TEXT NOT NULL,
                season         TEXT NOT NULL,
                map_name       TEXT NOT NULL,
                outcome        TEXT NOT NULL,
                rounds_won     INTEGER NOT NULL DEFAULT 0,
                rounds_lost    INTEGER NOT NULL DEFAULT 0,
                kills          INTEGER NOT NULL DEFAULT 0,
                deaths         INTEGER NOT NULL DEFAULT 0,
                headshots      INTEGER NOT NULL DEFAULT 0,
                damage         INTEGER NOT NULL DEFAULT 0,
                mvps           INTEGER NOT NULL DEFAULT 0,
                rating_before  REAL NOT NULL,
                rating_after   REAL NOT NULL,
                rating_delta   REAL NOT NULL,
                played_at      REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ranked_history_account "
            "ON hassault_ranked_history(account_id, played_at DESC)"
        )


def get_player_ranked_profile(account_id: str) -> dict[str, Any]:
    """Retrieve full ranked profile with calibration status and career stats."""
    init_ranked_seasons_db()
    base_rating = rating.get_player_rating(account_id)
    matches_played = base_rating.get("matchesPlayed", 0)
    is_calibrated = matches_played >= PLACEMENT_MATCHES_REQUIRED
    calib_remaining = max(0, PLACEMENT_MATCHES_REQUIRED - matches_played)

    tier_num = base_rating.get("tierNumber", 1)
    tier_info = TIER_DETAILS.get(tier_num, TIER_DETAILS[1])

    with get_db_conn() as conn:
        # Aggregated stats from ranked history
        row = conn.execute(
            """
            SELECT 
                COUNT(*) as total_logged,
                COALESCE(SUM(kills), 0) as total_kills,
                COALESCE(SUM(deaths), 0) as total_deaths,
                COALESCE(SUM(headshots), 0) as total_hs,
                COALESCE(SUM(damage), 0) as total_dmg,
                COALESCE(SUM(mvps), 0) as total_mvps,
                COALESCE(SUM(rounds_won + rounds_lost), 0) as total_rounds
            FROM hassault_ranked_history
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()

        # Recent 10 matches
        recent_rows = conn.execute(
            """
            SELECT * FROM hassault_ranked_history
            WHERE account_id = ?
            ORDER BY played_at DESC
            LIMIT 10
            """,
            (account_id,),
        ).fetchall()

    total_kills = row["total_kills"] if row else 0
    total_deaths = max(1, row["total_deaths"]) if row else 1
    total_hs = row["total_hs"] if row else 0
    total_dmg = row["total_dmg"] if row else 0
    total_mvps = row["total_mvps"] if row else 0
    total_rounds = max(1, row["total_rounds"]) if row else 1

    kd_ratio = round(total_kills / total_deaths, 2)
    hs_rate = round((total_hs / max(1, total_kills)) * 100.0, 1)
    adr = round(total_dmg / total_rounds, 1)
    wins = base_rating.get("wins", 0)
    losses = base_rating.get("losses", 0)
    win_rate = round((wins / max(1, wins + losses)) * 100.0, 1) if (wins + losses) > 0 else 0.0

    history = [dict(r) for r in recent_rows]

    display_tier = tier_info["tier"] if is_calibrated else f"Unranked ({matches_played}/{PLACEMENT_MATCHES_REQUIRED})"
    badge = tier_info["badge"] if is_calibrated else "unranked"

    return {
        "accountId": account_id,
        "season": CURRENT_SEASON,
        "rating": base_rating.get("rating", rating.DEFAULT_RATING),
        "rd": base_rating.get("rd", rating.DEFAULT_RD),
        "tier": display_tier,
        "tierName": tier_info["tier"],
        "tierNumber": tier_num,
        "tierBadge": badge,
        "isCalibrated": is_calibrated,
        "calibrationRemaining": calib_remaining,
        "matchesPlayed": matches_played,
        "wins": wins,
        "losses": losses,
        "winRate": win_rate,
        "kdRatio": kd_ratio,
        "adr": adr,
        "hsRate": hs_rate,
        "totalMvps": total_mvps,
        "lastChange": base_rating.get("lastChange", 0.0),
        "recentMatches": history,
    }


def record_ranked_match(
    account_id: str,
    match_id: str,
    map_name: str,
    result: dict[str, Any],
    opp_rating: float = rating.DEFAULT_RATING,
    opp_rd: float = 200.0,
) -> dict[str, Any]:
    """Record a competitive match result and update ELO/MMR rating."""
    init_ranked_seasons_db()
    current_profile = get_player_ranked_profile(account_id)
    rating_before = current_profile["rating"]

    # Use higher K-factor impact during calibration
    matches_played = current_profile["matchesPlayed"]
    if matches_played < PLACEMENT_MATCHES_REQUIRED:
        # Scale performance impact for placements
        calib_mult = 1.6
        adj_result = dict(result)
        adj_result["adr"] = float(result.get("adr", 100.0)) * calib_mult
        updated_rating = rating.update_player_rating(account_id, adj_result, opp_rating, opp_rd)
    else:
        updated_rating = rating.update_player_rating(account_id, result, opp_rating, opp_rd)

    rating_after = updated_rating["rating"]
    rating_delta = updated_rating["delta"]

    won = bool(result.get("won", False))
    rounds_won = int(result.get("roundsWon", 0))
    rounds_lost = int(result.get("roundsLost", 0))
    outcome = "WIN" if won else ("TIE" if rounds_won == rounds_lost else "LOSS")

    kills = int(result.get("kills", 0))
    deaths = int(result.get("deaths", 0))
    headshots = int(result.get("headshots", 0))
    damage = int(result.get("damage", 0))
    mvps = int(result.get("mvps", 1 if result.get("mvp") else 0))
    now = time.time()

    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO hassault_ranked_history
                (account_id, match_id, season, map_name, outcome, rounds_won, rounds_lost,
                 kills, deaths, headshots, damage, mvps, rating_before, rating_after, rating_delta, played_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                match_id,
                CURRENT_SEASON,
                map_name,
                outcome,
                rounds_won,
                rounds_lost,
                kills,
                deaths,
                headshots,
                damage,
                mvps,
                rating_before,
                rating_after,
                rating_delta,
                now,
            ),
        )

    return get_player_ranked_profile(account_id)


def get_ranked_leaderboard_extended(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve top competitive leaderboard entries with full profile metadata."""
    init_ranked_seasons_db()
    base_entries = rating.get_leaderboard(limit)
    out = []
    for entry in base_entries:
        acc_id = entry["account_id"]
        matches = entry.get("matches_played", 0)
        is_calib = matches >= PLACEMENT_MATCHES_REQUIRED
        tier_num = entry.get("tier_number", 1)
        tier_info = TIER_DETAILS.get(tier_num, TIER_DETAILS[1])
        wins = entry.get("wins", 0)
        losses = entry.get("losses", 0)
        win_rate = round((wins / max(1, wins + losses)) * 100.0, 1) if (wins + losses) > 0 else 0.0

        out.append({
            "rank": entry.get("rank", 1),
            "accountId": acc_id,
            "name": entry.get("name", acc_id[:8]),
            "rating": entry.get("rating", rating.DEFAULT_RATING),
            "tier": tier_info["tier"] if is_calib else f"Unranked ({matches}/{PLACEMENT_MATCHES_REQUIRED})",
            "tierName": tier_info["tier"],
            "tierBadge": tier_info["badge"] if is_calib else "unranked",
            "isCalibrated": is_calib,
            "matchesPlayed": matches,
            "wins": wins,
            "losses": losses,
            "winRate": win_rate,
            "lastChange": entry.get("last_change", 0.0),
        })
    return out
