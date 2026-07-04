"""Unit tests for the shared game engine (base contract + tic-tac-toe)."""

from __future__ import annotations

import pytest

from backend.games_engine import CHANCE, TERMINAL, get_game, list_games
from backend.games_engine.tictactoe import TicTacToe


def _play(state: TicTacToe, cells: list[int]) -> None:
    """Drive a game by applying the given cells for the current player each turn."""
    for cell in cells:
        player = state.current_player()
        assert player not in (CHANCE, TERMINAL)
        state.apply_action(player, str(cell))


def test_registry_lists_tictactoe() -> None:
    ids = {spec.id for spec in list_games()}
    assert "tictactoe" in ids
    spec = get_game("tictactoe")
    assert spec.min_players == 2 and spec.max_players == 2
    assert isinstance(spec.new(), TicTacToe)


def test_get_game_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_game("nope")


def test_initial_legal_actions_are_all_nine_cells() -> None:
    state = TicTacToe()
    assert state.current_player() == 0
    actions = state.legal_actions(0)
    assert sorted(int(a.id) for a in actions) == list(range(9))
    # The non-moving player has no legal actions.
    assert state.legal_actions(1) == []


def test_apply_action_alternates_turns_and_shrinks_legal_set() -> None:
    state = TicTacToe()
    state.apply_action(0, "4")  # X center
    assert state.current_player() == 1
    assert state.board[4] == 0
    assert all(a.id != "4" for a in state.legal_actions(1))


def test_illegal_moves_raise() -> None:
    state = TicTacToe()
    state.apply_action(0, "0")
    with pytest.raises(ValueError):
        state.apply_action(1, "0")  # occupied
    with pytest.raises(ValueError):
        state.apply_action(0, "1")  # not X's turn
    with pytest.raises(ValueError):
        state.apply_action(1, "99")  # off board


def test_row_win_is_detected_and_scored() -> None:
    state = TicTacToe()
    # X: 0,1,2 (top row); O: 3,4 interleaved.
    _play(state, [0, 3, 1, 4, 2])
    assert state.is_terminal()
    assert state.current_player() == TERMINAL
    assert state.public_state()["winner"] == 0
    assert state.returns() == {0: 1.0, 1: -1.0}
    # No further moves are legal once terminal.
    assert state.legal_actions(0) == []


def test_full_board_draw() -> None:
    state = TicTacToe()
    # A known drawn fill order:
    #  X O X
    #  X O O
    #  O X X
    _play(state, [0, 1, 2, 4, 3, 5, 7, 6, 8])
    assert state.is_terminal()
    assert state.public_state()["winner"] is None
    assert state.returns() == {0: 0.0, 1: 0.0}


def test_perfect_info_observation_equals_public_state() -> None:
    state = TicTacToe()
    state.apply_action(0, "0")
    # Tic-tac-toe hides nothing: each player's observation is the public state.
    assert state.observation(0) == state.public_state()
    assert state.observation(1) == state.public_state()
