"""Tests for the networked ViZDoom Duel engine + the policy/eval harness.

These spin up real native `vizdoom.DoomGame` processes (and, for the duel, ZDoom
netcode between two of them), so the whole module is skipped when the native wheel
isn't installed. Everything runs headless. Runs are bounded (small tick budgets) so
the suite stays fast, and the duel tolerates the degraded fallback so a CI box that
can't open the netcode still passes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("vizdoom", reason="vizdoom native wheel not installed")

from backend.games_engine.base import TERMINAL, get_game  # noqa: E402
from backend.games_engine.vizdoom_duel import MAX_TICKS, VizDoomDuel  # noqa: E402
from backend.games_engine.vizdoom_harness import (  # noqa: E402
    RandomPolicy,
    RuleBasedPolicy,
    ViZDoomHarness,
    evaluate_duel,
)


def test_duel_registered() -> None:
    spec = get_game("vizdoom_duel")
    assert spec.name == "ViZDoom Duel"
    assert (spec.min_players, spec.max_players) == (2, 2)


def test_harness_single_player() -> None:
    """The single-player eval harness runs a policy and reports score metrics."""
    harness = ViZDoomHarness("basic.cfg", render=False)
    try:
        # basic.cfg = [MOVE_LEFT, MOVE_RIGHT, ATTACK] → 3 one-hot actions.
        assert len(harness.actions) == 3
        metrics = harness.evaluate(
            RuleBasedPolicy(harness.action_space_size, harness.actions),
            num_episodes=2,
        )
        assert metrics["episodes"] == 2
        assert len(metrics["scores"]) == 2
        assert metrics["min_score"] <= metrics["mean_score"] <= metrics["max_score"]
    finally:
        harness.close()


def test_duel_engine_flow() -> None:
    game = VizDoomDuel()
    try:
        assert game.mode in ("duel", "degraded")
        # Simultaneous two-seat game: both seats act each tick.
        assert game.current_players() == [0, 1]
        assert game.tick == 0

        # Action ids come from the cig scenario's binary buttons; idle first, attack
        # present, and no continuous *_delta button leaks into the enumerated set.
        ids = game._action_ids
        assert ids[0] == "idle"
        assert "attack" in ids
        assert not any(a.endswith("_delta") for a in ids)

        # Per-seat observation carries a JPEG frame + HUD + legal actions.
        obs = game.observation(0)
        assert obs["game"] == "vizdoom_duel"
        assert obs["frame"].startswith("data:image/jpeg;base64,")
        assert set(obs["hud"]) == {"health", "ammo", "score"}

        # The tick only advances once *both* seats have acted (buffer pattern).
        game.apply_action(0, "attack")
        assert game.tick == 0 and game.current_players() == [1]
        game.apply_action(1, "idle")
        assert game.tick == 1

        # A seat can't act twice in one tick, and illegal ids are rejected.
        game.apply_action(0, "idle")
        with pytest.raises(ValueError):
            game.apply_action(0, "idle")
        with pytest.raises(ValueError):
            game.apply_action(1, "teleport")
        game.apply_action(1, "idle")

        # public_state exposes both frames + HUDs + the mode flag.
        pub = game.public_state()
        assert pub["mode"] == game.mode
        assert len(pub["frames"]) == 2 and len(pub["hud"]) == 2

        # Play out to terminal (bounded); the winner is frag-based and returns are
        # the usual +1/-1/0.
        while game.current_players() and game.tick < MAX_TICKS + 5:
            for seat in game.current_players():
                game.apply_action(seat, "attack")
        assert game.is_terminal()
        assert game.current_player() == TERMINAL
        returns = game.returns()
        assert set(returns) == {0, 1}
        assert game._winner() in (0, 1, None)
    finally:
        game.close()


def test_evaluate_duel_head_to_head() -> None:
    """Two policies play the real networked deathmatch; the result reports per-seat
    frags and a winner (or None on a tie)."""
    import os

    import vizdoom as vzd

    # Count the cig scenario's buttons WITHOUT init()ing a DoomGame — a full
    # init/close here, followed by the duel's networked init in the same process,
    # trips a native repeated-init crash on Windows. load_config alone is enough.
    probe = vzd.DoomGame()
    probe.load_config(os.path.join(vzd.scenarios_path, "cig.cfg"))
    n = len(probe.get_available_buttons())
    actions = [[i == j for j in range(n)] for i in range(n)]

    result = evaluate_duel(
        RandomPolicy(n, actions),
        RandomPolicy(n, actions),
        max_ticks=40,
    )
    assert result["mode"] in ("duel", "degraded")
    assert len(result["frags"]) == 2
    assert result["winner"] in (0, 1, None)
