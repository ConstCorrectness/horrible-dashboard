"""The Gymnasium seam: how a game's `GameState` becomes an RL environment.

The engine contract (`base.py`) is OpenSpiel-shaped — dicts, string action ids,
per-seat observations. Gymnasium wants arrays and integers. An `EnvAdapter` is the
translation, declared **per game** and attached to its `GameSpec`, so the engine
stays the source of truth and no game is forced into a shape it doesn't have.

Three things worth knowing, because each is a place this could have gone wrong:

**Only `decision_class == "policy"` games get an adapter.** For a `reasoner` game
the action is a *patch*, an *answer*, a golfed program — an open action carrying a
payload. `spaces.Discrete(n)` cannot describe that, and pretending otherwise would
be the same silent reinterpretation the database module's vector drivers refuse.
Reasoner games keep the open-action contract and simply have no `env`; the Train
section reads `adapter_for(...) is None` and offers the single-turn tools instead.

**Action sets are dynamic, so the action space is fixed and the mask moves.** Our
`legal_actions` changes every turn; Gymnasium has no concept for that. The
convention (PettingZoo's, and what every masked-PPO implementation expects) is a
constant `Discrete(n)` covering every action the game *can* ever have, plus an
`action_mask` in `info` marking which are legal right now. `mask_for` derives it
generically from `to_index`, so a game only declares the mapping once.

**Observations are encoded from a seat's point of view, not the board's.** A policy
that has to be told "you are player 1" has to learn each side separately, which is
exactly the thing self-play is supposed to avoid. `encode_obs(obs, seat)` returns
+1 for the seat's own pieces and -1 for the opponent's, so a policy trained as X
plays O for free. This is why the signature takes a seat at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np
from gymnasium import spaces


@dataclass(frozen=True)
class TrainingSpec:
    """What kind of training a game supports, so the Train UI is customisable per
    game rather than one fixed loop bolted onto everything.

    `self_play` is False for games whose engine is expensive or unsafe to run many
    times in-process — notably the native ViZDoom engines, whose repeated solo init
    is known to crash the process (see docs/modules/games.mdx). Such a game can
    still have an adapter (so a policy gets masks and encoded observations in a real
    match) while refusing to be looped a thousand times headlessly.
    """

    # May the episode runner loop this game headlessly at all?
    self_play: bool = True
    # Sensible default and ceiling for the episode count in the UI. The ceiling is a
    # guard on wall-clock, not on ambition: a fighter episode is ~1000 engine ticks
    # where a tic-tac-toe one is nine.
    default_episodes: int = 200
    max_episodes: int = 5_000
    # Does an in-app optimiser make sense here? True for small discrete games where
    # a tabular learner genuinely converges in-pane; False where the honest advice is
    # "train it properly against the Env in a notebook".
    in_app_optimizer: bool = False
    # Shown in the Train section as the suggested approach for this game.
    hint: str = ""


@dataclass(frozen=True)
class EnvAdapter:
    """A game's Gymnasium translation. `n_actions` is the width of the fixed
    `Discrete` space; `to_index` / `to_action_id` are inverses over it."""

    observation_space: spaces.Space[Any]
    n_actions: int
    # obs dict + the seat reading it -> the encoded observation.
    encode_obs: Callable[[dict[str, Any], int], np.ndarray]
    # engine action id -> index in [0, n_actions), and back.
    to_index: Callable[[str], int]
    to_action_id: Callable[[int], str]
    training: TrainingSpec = field(default_factory=TrainingSpec)

    @property
    def action_space(self) -> spaces.Discrete:
        return spaces.Discrete(self.n_actions)

    def mask_for(self, legal_actions: Sequence[Any]) -> np.ndarray:
        """The `action_mask` for a turn: 1 where the action is legal.

        Accepts engine `Action`s or their wire dicts, since the referee has both
        shapes on hand. An id that does not map into the space is skipped rather
        than raising — a game that grows an action its adapter doesn't know about
        should degrade to "that move is unavailable to policies", not crash a match.
        """
        mask = np.zeros(self.n_actions, dtype=np.int8)
        for action in legal_actions:
            raw = (
                action.get("id")
                if isinstance(action, dict)
                else getattr(action, "id", None)
            )
            if raw is None:
                continue
            try:
                index = self.to_index(str(raw))
            except (ValueError, KeyError):
                continue
            if 0 <= index < self.n_actions:
                mask[index] = 1
        return mask


# ---- shared encoders --------------------------------------------------------


def mark_grid_encoder(
    marks: Sequence[str], size: int, key: str = "board"
) -> Callable[[dict[str, Any], int], np.ndarray]:
    """An encoder for games whose observation is a flat (or nested) grid of mark
    strings, seat-relative: +1 mine, -1 theirs, 0 empty.

    Shared by tic-tac-toe and Connect Four because they differ only in shape, and
    the seat-relative sign convention is the part that must not drift between them.
    """
    mine_of = {mark: i for i, mark in enumerate(marks)}

    def encode(obs: dict[str, Any], seat: int) -> np.ndarray:
        raw = obs.get(key) or []
        cells: list[Any] = []
        for row in raw:
            if isinstance(row, list):
                cells.extend(row)
            else:
                cells.append(row)
        out = np.zeros(size, dtype=np.int8)
        for i, cell in enumerate(cells[:size]):
            owner = mine_of.get(cell) if isinstance(cell, str) else None
            if owner is None:
                continue
            out[i] = 1 if owner == seat else -1
        return out

    return encode


def int_id_adapter(
    n_actions: int,
    observation_space: spaces.Space[Any],
    encode_obs: Callable[[dict[str, Any], int], np.ndarray],
    training: TrainingSpec | None = None,
) -> EnvAdapter:
    """The common case: action ids are already the stringified index (`"0".."8"`
    for tic-tac-toe cells, `"0".."6"` for Connect Four columns)."""
    return EnvAdapter(
        observation_space=observation_space,
        n_actions=n_actions,
        encode_obs=encode_obs,
        to_index=int,
        to_action_id=str,
        training=training or TrainingSpec(),
    )


# ---- registry ---------------------------------------------------------------

_ADAPTERS: dict[str, EnvAdapter] = {}


def register_adapter(game_id: str, adapter: EnvAdapter) -> EnvAdapter:
    _ADAPTERS[game_id] = adapter
    return adapter


def adapter_for(game_id: str) -> EnvAdapter | None:
    """A game's adapter, or None when it has no RL environment — which is the
    correct, expected answer for every `reasoner` game."""
    return _ADAPTERS.get(game_id)


def games_with_env() -> list[str]:
    return sorted(_ADAPTERS)
