"""Tests for the ViZDoom game engine (real native Doom, server-rendered frames).

These spin up two headless `vizdoom.DoomGame` processes, so they're skipped if the
native wheel isn't installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("vizdoom", reason="vizdoom native wheel not installed")

from backend.games_engine.base import TERMINAL  # noqa: E402
from backend.games_engine.vizdoom_toy import MAX_TICKS, VizDoomGame  # noqa: E402


def test_vizdoom_engine_flow() -> None:
    game = VizDoomGame()
    try:
        # Simultaneous two-seat game: both seats act each tick.
        assert game.current_players() == [0, 1]
        assert game.tick == 0

        # Action space is derived from the scenario's buttons; idle is always first.
        ids = game._action_ids
        assert ids[0] == "idle"
        assert "attack" in ids

        # Per-seat observation carries a JPEG frame + HUD + legal actions.
        obs = game.observation(0)
        assert obs["game"] == "vizdoom_toy"
        assert obs["seat"] == 0
        assert obs["frame"].startswith("data:image/jpeg;base64,")
        assert set(obs["hud"]) == {"health", "ammo", "score"}
        assert [a["id"] for a in obs["legal_actions"]] == ids

        # The tick only advances once *both* seats have acted (the buffer pattern).
        game.apply_action(0, "attack")
        assert game.tick == 0  # seat 1 hasn't acted yet
        assert game.current_players() == [1]
        game.apply_action(1, "idle")
        assert game.tick == 1

        # A seat can't act twice in one tick.
        game.apply_action(0, "idle")
        with pytest.raises(ValueError):
            game.apply_action(0, "idle")
        game.apply_action(1, "idle")

        # Illegal action id is rejected.
        with pytest.raises(ValueError):
            game.apply_action(0, "teleport")

        # public_state exposes both frames + HUDs.
        pub = game.public_state()
        assert len(pub["frames"]) == 2
        assert all(f.startswith("data:image/jpeg;base64,") for f in pub["frames"])
        assert len(pub["hud"]) == 2

        # Play seat 0 (attacks) vs seat 1 (idles) to a terminal state; seat 0 should
        # score kills and win the race.
        while game.current_players() and game.tick < MAX_TICKS + 5:
            for seat in game.current_players():
                game.apply_action(seat, "attack" if seat == 0 else "idle")

        assert game.current_player() == TERMINAL
        assert game.is_terminal()
        assert game.score[0] > game.score[1]
        assert game.returns() == {0: 1.0, 1: -1.0}
    finally:
        game.close()


def test_vizdoom_tie_is_draw() -> None:
    game = VizDoomGame()
    try:
        # Both idle: neither scores, so it's a draw (winner None, zero returns).
        while game.current_players() and game.tick < MAX_TICKS + 5:
            for seat in game.current_players():
                game.apply_action(seat, "idle")
        assert game.is_terminal()
        assert game.score[0] == game.score[1]
        assert game.public_state()["winner"] is None
        assert game.returns() == {0: 0.0, 1: 0.0}
    finally:
        game.close()
