"""The 2D fighter engine: determinism, tick stepping, KO/timeout outcomes, and
the idle-first legal ordering the referee's timeout auto-play relies on."""

from __future__ import annotations

from backend.games_engine.base import TERMINAL
from backend.games_engine.fighter import ACTIONS, ROUND_TICKS, Fighter


def _play_streams(a_moves, b_moves) -> Fighter:
    game = Fighter()
    for a, b in zip(a_moves, b_moves):
        if game.current_player() == TERMINAL:
            break
        game.apply_action(0, a)
        if game.current_player() == TERMINAL:
            break
        game.apply_action(1, b)
    return game


def test_idle_is_first_legal_action() -> None:
    game = Fighter()
    legal = game.legal_actions(0)
    assert legal[0].id == "idle"  # the referee auto-plays legal[0] on timeout
    assert {a.id for a in legal} == set(ACTIONS)


def test_both_seats_act_each_tick() -> None:
    game = Fighter()
    assert sorted(game.current_players()) == [0, 1]
    game.apply_action(0, "right")
    assert game.current_players() == [1]  # seat 0 done this tick
    game.apply_action(1, "left")
    assert game.tick == 1  # the world stepped when the second seat acted


def test_determinism_same_streams_same_frames() -> None:
    a = ["right"] * 60
    b = ["left", "light"] * 30
    g1 = _play_streams(a, b)
    g2 = _play_streams(a, b)
    assert g1.public_state() == g2.public_state()
    assert g1.round_wins == g2.round_wins


# Enough "right" ticks to close START distance (100 units at speed 6 ≈ 17).
_CLOSE = ["right"] * 20


def _drive_pressure(game: Fighter, seat0=lambda g: None, max_ticks=4000) -> Fighter:
    """Seat 0 closes distance and mashes heavy each tick (re-approaching after
    round resets); seat 1 idles. Adaptive so it survives round position resets."""
    for _ in range(max_ticks):
        if game.current_player() == TERMINAL:
            break
        f0, f1 = game.fighters
        move = "right" if f1.x > f0.x else "left"
        if abs(f0.x - f1.x) <= 30:
            move = "heavy"
        game.apply_action(0, move)
        if game.current_player() != TERMINAL:
            game.apply_action(1, "idle")
    return game


def test_a_relentless_attacker_kos_a_passive_target() -> None:
    game = _drive_pressure(Fighter())
    assert game.current_player() == TERMINAL
    assert game._winner() == 0
    assert game.returns() == {0: 1.0, 1: -1.0}


def test_timeout_round_goes_to_higher_hp() -> None:
    # Seat 0 lands a few light hits per round then idles; the clock runs out and
    # the higher-hp seat (0, which took no damage) wins each round.
    game = Fighter()
    hits_this_round = 0
    for _ in range(ROUND_TICKS * 3 + 60):
        if game.current_player() == TERMINAL:
            break
        if game.tick == 0:
            hits_this_round = 0
        f0, f1 = game.fighters
        move = "idle"
        if hits_this_round < 2:
            move = "right" if abs(f0.x - f1.x) > 40 else "light"
            if move == "light":
                hits_this_round += 1
        game.apply_action(0, move)
        if game.current_player() != TERMINAL:
            game.apply_action(1, "idle")
    assert game.current_player() == TERMINAL
    assert game._winner() == 0


def test_block_reduces_damage() -> None:
    attacker = _play_streams(_CLOSE + ["heavy"], ["idle"] * 21)
    blocker = _play_streams(_CLOSE + ["heavy"], ["crouch_block"] * 21)
    # The blocked fighter (seat 1) kept more hp than the un-blocking one.
    assert blocker.fighters[1].hp > attacker.fighters[1].hp
