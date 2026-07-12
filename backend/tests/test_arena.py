"""Arena bot-coding: engine phases + a real (small) botsim round.

The engine test fakes botsim.simulate so it doesn't spawn processes; a separate
test drives one real round (needs the exec gate) to prove the subprocess harness
round-trips and crashes forfeit."""

from __future__ import annotations

from backend.games_engine import arena, botsim
from backend.games_engine.arena import ROUNDS, ArenaGame
from backend.games_engine.base import CHANCE, TERMINAL, WORK


def _fake_result(scores, winner):
    return botsim.ArenaResult(
        scores=scores,
        winner=winner,
        ticks=[{"p": [[0, 0], [8, 8]], "s": scores, "pellets": []}],
        forfeits=[False, False],
    )


def test_arena_three_rounds(monkeypatch) -> None:
    game = ArenaGame()
    # Seat 0's bot wins every round.
    monkeypatch.setattr(botsim, "simulate", lambda a, b, seed: _fake_result([5, 2], 0))
    for _ in range(ROUNDS):
        assert sorted(game.current_players()) == [0, 1]
        game.apply_action(0, "submit_bot", {"code": "def bot(o): return 'stay'"})
        game.apply_action(1, "submit_bot", {"code": "def bot(o): return 'stay'"})
        # Both bots in → a chance node seeds the round, then WORK simulates it.
        assert game.current_player() == CHANCE
        import random

        game.resolve_chance(random.Random(0))
        assert game.current_player() == WORK
        game.run_work()

    assert game.current_player() == TERMINAL
    assert game.round_wins == [3, 0]
    assert game._winner() == 0
    assert game.returns() == {0: 3.0, 1: -3.0}
    state = game.public_state()
    assert len(state["round_logs"]) == ROUNDS


def test_arena_previous_round_visible_for_iteration(monkeypatch) -> None:
    game = ArenaGame()
    monkeypatch.setattr(botsim, "simulate", lambda a, b, seed: _fake_result([1, 3], 1))
    import random

    game.apply_action(0, "submit_bot", {"code": "x"})
    game.apply_action(1, "submit_bot", {"code": "y"})
    game.resolve_chance(random.Random(1))
    game.run_work()
    # Round 2's observation carries round 1's log so a player can study the loss.
    obs = game.observation(0)
    assert obs["round"] == 2
    assert obs["last_round"]["winner"] == 1
    assert obs["round_wins"] == [0, 1]


def test_botsim_greedy_beats_a_passive_bot(monkeypatch) -> None:
    monkeypatch.setenv("GAMES_ENABLE_CODE_EXEC", "1")
    # The starter (greedy) bot collects pellets; a stay-forever bot scores nothing.
    result = botsim.simulate(arena.STARTER_BOT, "def bot(o): return 'stay'", seed=7)
    assert result.forfeits == [False, False]
    assert result.scores[0] > result.scores[1]
    assert result.winner == 0
    assert len(result.ticks) > 0


def test_botsim_a_dying_bot_forfeits(monkeypatch) -> None:
    monkeypatch.setenv("GAMES_ENABLE_CODE_EXEC", "1")
    # A bot that kills its own process mid-game forfeits (per-tick exceptions are
    # caught and play 'stay', but a hard process exit is a forfeit).
    killer = "import os\n\ndef bot(obs):\n    os._exit(1)\n"
    result = botsim.simulate(arena.STARTER_BOT, killer, seed=7)
    assert result.forfeits[1] is True
    assert result.winner == 0


def test_botsim_disabled_is_a_draw(monkeypatch) -> None:
    monkeypatch.delenv("GAMES_ENABLE_CODE_EXEC", raising=False)
    result = botsim.simulate(
        "def bot(o): return 'stay'", "def bot(o): return 'stay'", 1
    )
    assert result.winner is None
    assert result.forfeits == [True, True]
