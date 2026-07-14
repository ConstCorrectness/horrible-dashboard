"""A policy + evaluation harness for ViZDoom — the local dev kit for building the
brain you'll ship as a `vizdoom_duel` / `vizdoom_toy` loadout.

Two things live here:

- **Policies.** `BasePolicy` is the contract (`get_action(state) -> button vector`);
  `RandomPolicy` and `RuleBasedPolicy` are runnable baselines to measure against and
  a template to copy. A policy sees a raw `vizdoom.GameState` and returns a
  button-press vector (one of the harness's enumerated one-hot actions).
- **Harnesses.** `ViZDoomHarness` runs a policy through N single-player episodes on a
  bundled scenario and reports score metrics — the fast inner loop for iterating on a
  policy. `evaluate_duel` pits **two** policies head-to-head over the *same networked
  deathmatch* the `vizdoom_duel` game uses (via `vizdoom_duel.open_duel_pair`), so you
  can A/B two brains on a real shared map before laddering.

`vizdoom` is imported lazily so importing this module never hard-fails on a node
without the native wheel. Everything is headless by default (`render=False`) so it
runs in CI and on the Fly game-server image; pass `render=True` on a desktop to watch.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class BasePolicy:
    """Abstract base for every ViZDoom policy. Subclass and implement `get_action`.

    `actions` is the harness's enumerated action set: a list of one-hot button
    vectors (`[False, True, False]`) matching the loaded scenario's buttons, so a
    policy chooses *among legal presses* rather than spelling a raw vector out.
    """

    def __init__(self, action_space_size: int, actions: list[list[bool]]) -> None:
        self.action_space_size = action_space_size
        self.actions = actions

    def get_action(self, state: Any) -> list[bool]:
        """Given the current `vizdoom.GameState`, return a button-press vector (one
        of `self.actions`), e.g. `[True, False, False]`."""
        raise NotImplementedError("You must implement the get_action method!")


class RandomPolicy(BasePolicy):
    """Baseline: press a uniformly random action every tick."""

    def get_action(self, state: Any) -> list[bool]:
        return random.choice(self.actions)


class RuleBasedPolicy(BasePolicy):
    """Heuristic baseline for a 3-button scenario ([MOVE_LEFT, MOVE_RIGHT, ATTACK],
    e.g. `basic`/`defend_the_center`): mostly shoot, occasionally sweep aim by
    flipping strafe direction. A template for reading `state.game_variables`."""

    def __init__(self, action_space_size: int, actions: list[list[bool]]) -> None:
        super().__init__(action_space_size, actions)
        self.MOVE_LEFT = actions[0]
        self.MOVE_RIGHT = actions[1]
        self.ATTACK = actions[2]
        self.last_move = self.MOVE_LEFT

    def get_action(self, state: Any) -> list[bool]:
        # state.game_variables holds whatever the .cfg exposes (e.g. AMMO2) — read it
        # here to make ammo-aware decisions; this baseline just keeps up the pressure.
        if random.random() < 0.6:
            return self.ATTACK
        if random.random() < 0.15:
            self.last_move = (
                self.MOVE_RIGHT if self.last_move == self.MOVE_LEFT else self.MOVE_LEFT
            )
        return self.last_move


def _make_game(vzd: Any, config_name: str, render: bool) -> Any:
    config_path = os.path.join(vzd.scenarios_path, config_name)
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config not found at: {config_path}")
    game = vzd.DoomGame()
    game.load_config(config_path)
    game.set_window_visible(render)
    return game


def _one_hot_actions(n: int) -> list[list[bool]]:
    """`n` one-hot button vectors (press exactly one button), matching the scenario's
    available buttons in order."""
    return [[i == j for j in range(n)] for i in range(n)]


class ViZDoomHarness:
    """Runs a single-player policy through episodes and reports score metrics.

    The fast inner loop for building a policy before you ship it as a loadout.
    Headless by default so it runs anywhere; `render=True` opens a window to watch.
    """

    def __init__(self, config_name: str = "basic.cfg", render: bool = False) -> None:
        import vizdoom as vzd  # lazy — native wheel only needed to actually run

        self._vzd = vzd
        self.game = _make_game(vzd, config_name, render)
        self.game.init()
        self.available_buttons = self.game.get_available_buttons()
        self.action_space_size = len(self.available_buttons)
        self.actions = _one_hot_actions(self.action_space_size)

    def evaluate(
        self,
        policy: BasePolicy,
        num_episodes: int = 5,
        sleep_time: float = 0.0,
    ) -> dict[str, Any]:
        """Run `policy` for `num_episodes` and return score metrics. `sleep_time`
        throttles the loop for watchable rendering (leave 0 for fast eval)."""
        scores: list[float] = []
        for episode in range(num_episodes):
            self.game.new_episode()
            episode_reward = 0.0
            while not self.game.is_episode_finished():
                state = self.game.get_state()
                action = policy.get_action(state)
                episode_reward += self.game.make_action(action)
                if sleep_time > 0:
                    time.sleep(sleep_time)
            scores.append(episode_reward)
            logger.info(
                "episode %d/%d score=%s", episode + 1, num_episodes, episode_reward
            )
        return {
            "episodes": num_episodes,
            "mean_score": sum(scores) / len(scores) if scores else 0.0,
            "max_score": max(scores) if scores else 0.0,
            "min_score": min(scores) if scores else 0.0,
            "scores": scores,
        }

    def close(self) -> None:
        try:
            self.game.close()
        except Exception:
            pass


def evaluate_duel(
    policy_a: BasePolicy,
    policy_b: BasePolicy,
    *,
    max_ticks: int = 300,
    tics_per_action: int = 2,
    render: bool = False,
) -> dict[str, Any]:
    """Pit two policies head-to-head over the **networked deathmatch** the
    `vizdoom_duel` game uses: one shared `cig` map, real frags. Each marine runs in
    its own free-running thread (ViZDoom async multiplayer — see vizdoom_duel), each
    calling its policy every tick. Returns per-seat frags and the winner (0/1/None).

    Falls back to the same degraded independent-instance mode as the engine if the
    netcode can't connect."""
    import vizdoom as vzd  # lazy

    from backend.games_engine import vizdoom_duel as duel

    try:
        games = duel.open_duel_pair(vzd, visible=render)
        mode = "duel"
    except Exception:
        logger.warning("duel netcode unavailable; degraded eval", exc_info=True)
        games = duel._open_solo_pair(vzd, visible=render)
        mode = "degraded"

    policies = [policy_a, policy_b]
    stop = threading.Event()

    def _run(seat: int) -> None:
        g = games[seat]
        pol = policies[seat]
        ticks = 0
        while not stop.is_set() and ticks < max_ticks:
            try:
                if g.is_episode_finished():
                    break
                if g.is_player_dead():
                    g.respawn_player()
                state = g.get_state()
                action = pol.get_action(state) if state is not None else pol.actions[0]
                g.make_action(action, tics_per_action)
                ticks += 1
            except Exception:
                logger.debug("duel eval worker %s errored", seat, exc_info=True)
                break

    threads = [threading.Thread(target=_run, args=(i,), daemon=True) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(max_ticks * tics_per_action * 0.05 + 30.0)
    stop.set()

    frags = [float(g.get_game_variable(vzd.GameVariable.FRAGCOUNT)) for g in games]
    for g in games:
        try:
            g.close()
        except Exception:
            pass

    winner: int | None = (
        0 if frags[0] > frags[1] else 1 if frags[1] > frags[0] else None
    )
    return {"mode": mode, "frags": frags, "winner": winner}
