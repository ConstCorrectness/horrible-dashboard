"""`HorribleEnv` — a real `gymnasium.Env` over any game that declares an adapter.

This is the **training** half of the contract. The playing half is a policy
(`act(obs, info) -> int`), and the two are different objects on purpose:

Gymnasium inverts control — the agent calls `env.step()` and the world advances on
demand. A ranked match cannot work that way: the opponent is a person on another
machine, and the world advances when the *server* says so. So a live seat is driven
by `BotPolicy` calling your `act`, while this Env exists for the loop you run
yourself — against a practice bot, or against your own policy. Train here, ship the
same `act` to the ladder. That split is exactly how RL practice already works
(`model.predict(obs)` is the policy; the Env is the gym), so nothing has been
invented here.

The opponent is **inside** the environment, which is what makes this a single-agent
Env rather than a multi-agent one: from your policy's point of view the opponent is
part of the world's dynamics. `reset` and `step` therefore run the opponent's moves
(and any chance/work nodes) until it is your turn again or the game is over.

Illegal actions are **not** silently repaired. `info["action_mask"]` tells you what
is legal every step; choosing outside it ends the episode with reward -1 and
`info["illegal"] = True`. Substituting a random legal move instead — the tempting
"be helpful" choice — would hide the single most common bug in a new policy behind
a slightly worse win rate, which is the opposite of educational. It ends the episode
rather than raising so a 1000-episode run still finishes and can *report* the count.
"""

from __future__ import annotations

import random
from typing import Any, Callable

import gymnasium as gym
import numpy as np

from backend.games_engine.base import CHANCE, TERMINAL, WORK, GameState, get_game
from backend.games_engine.env_adapter import EnvAdapter, adapter_for

# Picks the opponent's move: (observation, legal_action_dicts, seat) -> action id.
OpponentFn = Callable[[dict[str, Any], list[dict[str, Any]], int], str]

# A hard stop on plies per episode. A buggy engine or an opponent that never
# progresses would otherwise hang the whole episode runner; this surfaces as a
# `truncated` episode, which is precisely what Gymnasium's truncation flag is for.
MAX_PLIES = 10_000


def random_opponent(rng: random.Random | None = None) -> OpponentFn:
    """The default sparring partner: uniform over legal moves."""
    r = rng or random.Random()

    def choose(_obs: dict[str, Any], legal: list[dict[str, Any]], _seat: int) -> str:
        return str(r.choice(legal)["id"])

    return choose


class HorribleEnv(gym.Env[np.ndarray, np.int64]):
    """One seat of a two-player game, as a Gymnasium environment.

    `seat` is which side you play. Alternate it across episodes when evaluating —
    going first is worth a lot in most of these games, and a win rate measured only
    as X is not a win rate.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        game_id: str,
        *,
        seat: int = 0,
        opponent: OpponentFn | None = None,
        seed: int | None = None,
    ) -> None:
        adapter = adapter_for(game_id)
        if adapter is None:
            raise ValueError(
                f"{game_id!r} has no RL environment: it is a reasoner game whose "
                "actions are payloads (a patch, an answer), not points in a space"
            )
        self.game_id = game_id
        self.adapter: EnvAdapter = adapter
        self.seat = seat
        self.observation_space = adapter.observation_space
        self.action_space = adapter.action_space
        self._rng = random.Random(seed)
        self._opponent = opponent or random_opponent(self._rng)
        self._state: GameState | None = None
        self._plies = 0

    # ---- gymnasium API -------------------------------------------------------

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, dict[str, Any]]:
        super().reset(seed=seed)
        if seed is not None:
            # Reseed **in place**. Rebinding `self._rng = random.Random(seed)` looks
            # equivalent and is not: the default opponent captured this object at
            # construction, so a new one would leave the opponent running off the
            # old stream and make seeded resets non-reproducible. Gymnasium's
            # `check_env` fails on exactly that.
            self._rng.seed(seed)
        self._state = get_game(self.game_id).new()
        self._plies = 0
        self._advance_to_me()
        return self._obs(), self._info()

    def step(
        self, action: np.int64 | int
    ) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        state = self._require_state()
        info = self._info()
        index = int(action)

        if not info["action_mask"][index]:
            # See the module docstring: reported, never repaired.
            return self._obs(), -1.0, True, False, {**info, "illegal": True}

        state.apply_action(self.seat, self.adapter.to_action_id(index))
        self._plies += 1
        self._advance_to_me()

        terminated = state.is_terminal()
        truncated = not terminated and self._plies >= MAX_PLIES
        reward = float(state.returns().get(self.seat, 0.0)) if terminated else 0.0
        return self._obs(), reward, terminated, truncated, self._info()

    # ---- internals -----------------------------------------------------------

    def _require_state(self) -> GameState:
        if self._state is None:
            raise RuntimeError("step() before reset()")
        return self._state

    def _advance_to_me(self) -> None:
        """Run the world forward — chance nodes, server-side work, and the
        opponent's moves — until it is our turn or the game is over."""
        state = self._require_state()
        while self._plies < MAX_PLIES:
            current = state.current_player()
            if current in (TERMINAL, self.seat):
                return
            if current == CHANCE:
                state.resolve_chance(self._rng)
                continue
            if current == WORK:
                state.run_work()
                continue
            legal = [a.to_wire() for a in state.legal_actions(current)]
            if not legal:
                return
            choice = self._opponent(state.observation(current), legal, current)
            state.apply_action(current, choice)
            self._plies += 1

    def _obs(self) -> np.ndarray:
        state = self._require_state()
        return self.adapter.encode_obs(state.observation(self.seat), self.seat)

    def _info(self) -> dict[str, Any]:
        """`action_mask` is the load-bearing field — it is how a policy knows what
        it may do, and what masked-PPO implementations look for by name.

        `raw_obs` rides along because the same policy code runs in a live match,
        where it is handed the engine's observation dict rather than an array; a
        heuristic bot reads `raw_obs` and ignores the encoding entirely.
        """
        state = self._require_state()
        legal = (
            [a.to_wire() for a in state.legal_actions(self.seat)]
            if state.current_player() == self.seat
            else []
        )
        raw = state.observation(self.seat)
        # These keys must match `bot_sdk.build_info` exactly. A policy trained here
        # and shipped to the ladder reads the same dict in both places; a key that
        # exists in one and not the other is a bug that only shows up in a rated
        # match, which is the worst possible place to find it.
        return {
            "action_mask": self.adapter.mask_for(legal),
            "legal_actions": legal,
            "obs": self.adapter.encode_obs(raw, self.seat),
            "raw_obs": raw,
            "seat": self.seat,
            "game_id": self.game_id,
        }


def make_env(game_id: str, **kwargs: Any) -> HorribleEnv:
    """Construct a game's environment. The name every snippet and doc uses, so the
    constructor stays free to change."""
    return HorribleEnv(game_id, **kwargs)
