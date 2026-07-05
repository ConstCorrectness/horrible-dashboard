"""Unit tests for the shared game engine (base contract + tic-tac-toe + connect four)."""

from __future__ import annotations

import pytest

from backend.games_engine import CHANCE, TERMINAL, get_game, list_games
from backend.games_engine.connect_four import COLS, ROWS, ConnectFour
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


# ---- connect four ----------------------------------------------------------


def _c4_from(rows: list[str], turn: int) -> ConnectFour:
    """Build a Connect Four state from a top-to-bottom board of 'R'/'Y'/'.' chars."""
    seat = {"R": 0, "Y": 1}
    st = ConnectFour()
    for r in range(ROWS):
        line = rows[ROWS - 1 - r]  # rows are top-first; grid[0] is the bottom row
        for c in range(COLS):
            st.grid[r][c] = None if line[c] == "." else seat[line[c]]
    st.turn = turn
    return st


def test_registry_lists_connect_four() -> None:
    spec = get_game("connect_four")
    assert spec.min_players == 2 and spec.max_players == 2
    assert isinstance(spec.new(), ConnectFour)


def test_drops_stack_and_full_column_is_illegal() -> None:
    st = ConnectFour()
    assert st.current_player() == 0
    assert sorted(int(a.id) for a in st.legal_actions(0)) == list(range(COLS))
    # Fill column 0 (six discs, alternating seats via the turn order is fine).
    for expected_turn in range(ROWS):
        st.apply_action(st.current_player(), "0")
    # Column 0 is now full, so it drops out of the legal set.
    assert all(a.id != "0" for a in st.legal_actions(st.current_player()))
    with pytest.raises(ValueError):
        st.apply_action(st.current_player(), "0")


def test_off_board_and_wrong_turn_raise() -> None:
    st = ConnectFour()
    with pytest.raises(ValueError):
        st.apply_action(0, "7")  # off board
    with pytest.raises(ValueError):
        st.apply_action(1, "3")  # not Yellow's turn


def test_horizontal_win_detected_and_scored() -> None:
    st = _c4_from(["......."] * 5 + ["RRR.YY."], 0)
    assert not st.is_terminal()
    st.apply_action(0, "3")  # complete cols 0-3 on the bottom row
    assert st.is_terminal()
    assert st.public_state()["winner"] == 0
    assert st.returns() == {0: 1.0, 1: -1.0}
    assert st.legal_actions(0) == []


def test_vertical_win_detected() -> None:
    st = _c4_from(["......."] * 3 + ["..R....", "..RY...", "..RYY.."], 0)
    st.apply_action(0, "2")  # fourth Red disc stacked in column 2
    assert st.is_terminal()
    assert st.public_state()["winner"] == 0


def test_diagonal_win_detected() -> None:
    # A rising diagonal for Red through (col0,row0)…(col3,row3).
    st = _c4_from(
        [
            ".......",
            ".......",
            "...R...",
            "..RY...",
            ".RYR...",
            "RYYY...",
        ],
        0,
    )
    assert st.public_state()["winner"] == 0
    assert st.current_player() == TERMINAL


def test_board_orientation_is_top_first() -> None:
    st = ConnectFour()
    st.apply_action(0, "0")  # Red lands on the bottom row
    board = st.public_state()["board"]
    assert len(board) == ROWS and len(board[0]) == COLS
    assert board[-1][0] == "R"  # bottom row, first column
    assert board[0][0] is None  # top row is still empty


def test_connect_four_has_no_chance_node() -> None:
    st = ConnectFour()
    assert st.current_player() not in (CHANCE, TERMINAL)
    assert st.observation(0) == st.public_state()
