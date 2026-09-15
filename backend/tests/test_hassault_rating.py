"""Unit tests for Ranked Glicko-2 Elo MMR Engine."""

from __future__ import annotations

import pytest
from backend.modules.hassault import rating


def test_tier_for_rating() -> None:
    assert rating.tier_for_rating(2500.0) == ("The Global Elite", 18)
    assert rating.tier_for_rating(2250.0) == ("Supreme Master First Class", 17)
    assert rating.tier_for_rating(1550.0) == ("Master Guardian II", 12)
    assert rating.tier_for_rating(1250.0) == ("Gold Nova III", 9)
    assert rating.tier_for_rating(750.0) == ("Silver IV", 4)
    assert rating.tier_for_rating(400.0) == ("Silver I", 1)


def test_glicko2_step_win_increases_rating() -> None:
    new_r, new_rd, new_vol = rating.compute_glicko2_step(
        rating=1500.0,
        rd=200.0,
        vol=0.06,
        opp_rating=1500.0,
        opp_rd=200.0,
        actual_score=1.0,
    )
    assert new_r > 1500.0
    assert new_rd < 200.0  # RD should decrease with match played
    assert new_vol > 0.0


def test_glicko2_step_loss_decreases_rating() -> None:
    new_r, new_rd, new_vol = rating.compute_glicko2_step(
        rating=1500.0,
        rd=200.0,
        vol=0.06,
        opp_rating=1500.0,
        opp_rd=200.0,
        actual_score=0.0,
    )
    assert new_r < 1500.0
    assert new_rd < 200.0


def test_performance_score_scaling() -> None:
    # Dominant win with high ADR & first bloods
    high_perf = rating.calculate_performance_score({
        "won": True,
        "roundsWon": 13,
        "roundsLost": 2,
        "adr": 145.0,
        "firstBloods": 4,
        "nutshotKills": 2,
        "hsRate": 0.65,
        "mvp": True,
    })

    # Close loss with low stats
    low_perf = rating.calculate_performance_score({
        "won": False,
        "roundsWon": 11,
        "roundsLost": 13,
        "adr": 42.0,
        "firstBloods": 0,
        "nutshotKills": 0,
        "hsRate": 0.10,
        "mvp": False,
    })

    assert high_perf > 0.85
    assert low_perf < 0.40
    assert high_perf > low_perf


def test_update_and_get_player_rating() -> None:
    acc_id = "test_player_ranked_01"
    initial = rating.get_player_rating(acc_id)
    assert initial["rating"] == 1500.0
    assert initial["matchesPlayed"] == 0

    # Win match
    res = rating.update_player_rating(
        acc_id,
        {
            "won": True,
            "roundsWon": 13,
            "roundsLost": 5,
            "adr": 120.0,
            "firstBloods": 2,
            "nutshotKills": 1,
            "hsRate": 0.50,
            "mvp": True,
        },
    )

    assert res["rating"] > 1500.0
    assert res["matchesPlayed"] == 1
    assert res["wins"] == 1
    assert res["losses"] == 0
    assert res["delta"] > 0.0

    # Fetch back
    fetched = rating.get_player_rating(acc_id)
    assert fetched["rating"] == res["rating"]
    assert fetched["matchesPlayed"] == 1

    # Leaderboard includes player
    lb = rating.get_leaderboard(limit=10)
    assert any(p["account_id"] == acc_id for p in lb)
