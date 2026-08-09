"""Connect Four: a second perfect-information board game.

Seven columns, six rows, gravity — a disc dropped in a column falls to the lowest
empty cell. Same `GameState` contract as tic-tac-toe (no chance, no hidden state, so
`observation` == `public_state`), so it plugs into the referee, ladder, and challenge
track unchanged. It broadens the game gamut and gives the challenge track a second
game with a richer tactical vocabulary (threats, forks) than tic-tac-toe.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from gymnasium import spaces

from backend.games_engine.base import (
    TERMINAL,
    Action,
    GameSpec,
    GameState,
    register_game,
)
from backend.games_engine.env_adapter import (
    TrainingSpec,
    int_id_adapter,
    mark_grid_encoder,
    register_adapter,
)

COLS = 7
ROWS = 6
_MARKS = ("R", "Y")  # seat 0 = Red (moves first), seat 1 = Yellow
_NEED = 4  # discs in a line to win
# The four directions a line can run (scanning up/right avoids double counting).
_DIRS = ((0, 1), (1, 0), (1, 1), (1, -1))


class ConnectFour(GameState):
    def __init__(self) -> None:
        # grid[r][c]: None, or the seat that owns the cell. r=0 is the BOTTOM row
        # (the gravity floor), so a drop fills the lowest empty r in a column.
        self.grid: list[list[int | None]] = [[None] * COLS for _ in range(ROWS)]
        self.turn: int = 0  # Red moves first

    # ---- turn structure ----------------------------------------------------

    def current_player(self) -> int:
        if self._winner() is not None or self._full():
            return TERMINAL
        return self.turn

    def legal_actions(self, player: int) -> list[Action]:
        if player != self.current_player():
            return []
        return [
            Action(id=str(c), label=f"drop {_MARKS[player]} in column {c}")
            for c in range(COLS)
            if self.grid[ROWS - 1][c] is None  # column not full
        ]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player != self.current_player():
            raise ValueError("not this player's turn")
        try:
            col = int(action_id)
        except ValueError as exc:
            raise ValueError(f"bad action id {action_id!r}") from exc
        if not 0 <= col < COLS:
            raise ValueError(f"column {col} out of range")
        row = self._drop_row(col)
        if row is None:
            raise ValueError(f"column {col} is full")
        self.grid[row][col] = player
        self.turn = 1 - player

    # ---- views -------------------------------------------------------------

    def public_state(self) -> dict[str, Any]:
        # `board` is emitted top row first so the UI renders the grid as-is.
        board = [
            [None if cell is None else _MARKS[cell] for cell in self.grid[r]]
            for r in range(ROWS - 1, -1, -1)
        ]
        return {
            "game": "connect_four",
            "cols": COLS,
            "rows": ROWS,
            "board": board,
            "turn": None if self.is_terminal() else self.turn,
            "winner": self._winner(),
        }

    # ---- outcome -----------------------------------------------------------

    def returns(self) -> dict[int, float]:
        w = self._winner()
        if w is None:
            return {0: 0.0, 1: 0.0}  # draw (or not terminal — undefined by contract)
        return {w: 1.0, 1 - w: -1.0}

    # ---- helpers -----------------------------------------------------------

    def _drop_row(self, col: int) -> int | None:
        """The row a disc dropped in `col` would land in, or None if the column is full."""
        for r in range(ROWS):
            if self.grid[r][col] is None:
                return r
        return None

    def _full(self) -> bool:
        return all(self.grid[ROWS - 1][c] is not None for c in range(COLS))

    def _winner(self) -> int | None:
        for r in range(ROWS):
            for c in range(COLS):
                seat = self.grid[r][c]
                if seat is not None and any(
                    self._line(r, c, dr, dc, seat) for dr, dc in _DIRS
                ):
                    return seat
        return None

    def _line(self, r: int, c: int, dr: int, dc: int, seat: int) -> bool:
        """True if `seat` owns _NEED cells running from (r, c) in direction (dr, dc)."""
        for k in range(_NEED):
            rr, cc = r + dr * k, c + dc * k
            if not (0 <= rr < ROWS and 0 <= cc < COLS) or self.grid[rr][cc] != seat:
                return False
        return True


SPEC = register_game(
    GameSpec(
        id="connect_four",
        name="Connect Four",
        min_players=2,
        max_players=2,
        factory=ConnectFour,
        # Turn-based coded-agent game on the escape hatch — see tictactoe.
        decision_class="policy",
        declared_policies=("agent", "bot", "random", "manual"),
        default_policy="agent",
    )
)

# 42 cells, 7 actions (one per column). The observation is the grid **as the wire
# emits it** — top row first — because that is what a policy is handed in a live
# match; encoding a different orientation here than `public_state` produces would
# train a policy on a board it never actually sees.
ADAPTER = register_adapter(
    "connect_four",
    int_id_adapter(
        n_actions=COLS,
        observation_space=spaces.Box(
            low=-1, high=1, shape=(ROWS * COLS,), dtype=np.int8
        ),
        encode_obs=mark_grid_encoder(_MARKS, size=ROWS * COLS),
        training=TrainingSpec(
            default_episodes=100,
            max_episodes=2_000,
            # 4.5 trillion states: a tabular learner is the wrong tool and would
            # only teach the wrong lesson.
            in_app_optimizer=False,
            hint=(
                "Too large to tabulate — write a heuristic (threats, forks, centre "
                "control) or train a net against the Env in a notebook."
            ),
        ),
    ),
)
