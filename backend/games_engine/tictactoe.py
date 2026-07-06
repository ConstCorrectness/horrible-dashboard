"""Tic-Tac-Toe: the reference perfect-information game.

Deliberately the simplest possible implementation of the `GameState` contract —
no chance, no hidden state, so `observation` == `public_state`. It exists to prove
the whole pipeline (matchmaking → referee → agent picks a legal move → spectate)
end to end before the harder games (chess, poker) plug into the same interface.
"""

from __future__ import annotations

from typing import Any

from backend.games_engine.base import (
    TERMINAL,
    Action,
    GameSpec,
    GameState,
    register_game,
)

# The eight lines that win.
_LINES = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)
_MARKS = ("X", "O")


class TicTacToe(GameState):
    def __init__(self) -> None:
        # board[i] is None, or the seat index (0 = X, 1 = O) that owns cell i.
        self.board: list[int | None] = [None] * 9
        self.turn: int = 0  # seat to move (X moves first)

    # ---- turn structure ----------------------------------------------------

    def current_player(self) -> int:
        if self._winner() is not None or self._full():
            return TERMINAL
        return self.turn

    def legal_actions(self, player: int) -> list[Action]:
        if player != self.current_player():
            return []
        return [
            Action(id=str(i), label=f"place {_MARKS[player]} at {i}")
            for i in range(9)
            if self.board[i] is None
        ]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player != self.current_player():
            raise ValueError("not this player's turn")
        try:
            cell = int(action_id)
        except ValueError as exc:
            raise ValueError(f"bad action id {action_id!r}") from exc
        if not 0 <= cell <= 8 or self.board[cell] is not None:
            raise ValueError(f"cell {cell} is not empty")
        self.board[cell] = player
        self.turn = 1 - player

    # ---- views -------------------------------------------------------------

    def public_state(self) -> dict[str, Any]:
        return {
            "game": "tictactoe",
            "board": [None if c is None else _MARKS[c] for c in self.board],
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

    def _winner(self) -> int | None:
        for a, b, c in _LINES:
            if (
                self.board[a] is not None
                and self.board[a] == self.board[b] == self.board[c]
            ):
                return self.board[a]
        return None

    def _full(self) -> bool:
        return all(c is not None for c in self.board)


SPEC = register_game(
    GameSpec(
        id="tictactoe",
        name="Tic-Tac-Toe",
        min_players=2,
        max_players=2,
        factory=TicTacToe,
    )
)
