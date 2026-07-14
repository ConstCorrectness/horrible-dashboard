"""The **challenge track**: category scenarios that grade an agent harness off-table.

A challenge is a fixed position (`observation` + `legal_actions`) with a hidden set of
**acceptable answers**. The server sends the node only the position — never the answer —
so a node runs its harness on each scenario and the server grades the returned choices.
That's the same anti-cheat shape as the referee: the solution never leaves the server.

Scenarios are built from the real engine state, so the observation/legal-action shapes
exactly match a live game. Categories (`win`, `block`, `center`, …) are the "various
categories" a good harness must handle; the report scores correctness per category and
counts how many categories are covered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.games_engine.connect_four import COLS, ROWS, ConnectFour
from backend.games_engine.holdem import Holdem
from backend.games_engine.tictactoe import TicTacToe

# 'X' = seat 0, 'O' = seat 1, '.' = empty.
_SEAT = {"X": 0, "O": 1}
# Connect Four: 'R' = seat 0 (Red, moves first), 'Y' = seat 1, '.' = empty.
_C4_SEAT = {"R": 0, "Y": 1}


@dataclass(frozen=True)
class Challenge:
    id: str
    game_id: str
    category: str
    description: str
    observation: dict[str, Any]
    legal_actions: list[dict[str, Any]]
    solution: list[str]  # acceptable action ids (hidden from the node)

    def public(self) -> dict[str, Any]:
        """What the node is told — everything except the solution."""
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "observation": self.observation,
            "legal_actions": self.legal_actions,
        }


def _ttt(
    id: str, category: str, description: str, board: str, turn: int, solution: list[str]
) -> Challenge:
    """Build a tic-tac-toe challenge from a board string ('XXO.O....'), so the
    observation + legal actions come straight from the engine."""
    st = TicTacToe()
    st.board = [None if c == "." else _SEAT[c] for c in board]
    st.turn = turn
    return Challenge(
        id=id,
        game_id="tictactoe",
        category=category,
        description=description,
        observation=st.observation(turn),
        legal_actions=[a.to_wire() for a in st.legal_actions(turn)],
        solution=solution,
    )


# The bundled sets. Keep solutions unambiguous so grading is objective.
_TTT_CHALLENGES: list[Challenge] = [
    # win: X already has two in a line — take the third and win outright.
    _ttt(
        "ttt-win-row",
        "win",
        "X to move: complete the top row and win.",
        "XX.OO....",
        0,
        ["2"],
    ),
    _ttt(
        "ttt-win-diag",
        "win",
        "X to move: complete the diagonal and win.",
        "X..OXO...",
        0,
        ["8"],
    ),
    # block: O threatens to win next move — X must block, not wander.
    _ttt(
        "ttt-block-row",
        "block",
        "X to move: O threatens the top row — block it.",
        "OO..X....",
        0,
        ["2"],
    ),
    _ttt(
        "ttt-block-col",
        "block",
        "X to move: O threatens the left column — block it.",
        "O..O.X...",
        0,
        ["6"],
    ),
    # center: on an empty board, the center is the strongest opening.
    _ttt(
        "ttt-center",
        "center",
        "X to move on an empty board: take the strongest opening.",
        ".........",
        0,
        ["4"],
    ),
]


def _c4(
    id: str,
    category: str,
    description: str,
    rows: list[str],
    turn: int,
    solution: list[str],
) -> Challenge:
    """Build a Connect Four challenge from a top-to-bottom board (six rows of seven
    'R'/'Y'/'.' chars). The engine derives the observation + legal actions, so the
    scenario shape exactly matches a live game. Positions are gravity-valid."""
    st = ConnectFour()
    for r in range(ROWS):
        line = rows[ROWS - 1 - r]  # rows are top-first; grid[0] is the bottom row
        for c in range(COLS):
            ch = line[c]
            st.grid[r][c] = None if ch == "." else _C4_SEAT[ch]
    st.turn = turn
    return Challenge(
        id=id,
        game_id="connect_four",
        category=category,
        description=description,
        observation=st.observation(turn),
        legal_actions=[a.to_wire() for a in st.legal_actions(turn)],
        solution=solution,
    )


# Connect Four scenarios. 'win'/'block'/'center' mirror tic-tac-toe; 'double' is the
# new tactical category — a single drop that makes two simultaneous threats (an
# open-ended three) the opponent can't both block.
_C4_CHALLENGES: list[Challenge] = [
    # win: Red already has three in a line — complete the fourth and win outright.
    _c4(
        "c4-win-row",
        "win",
        "Red to move: complete the bottom row and win.",
        [
            ".......",
            ".......",
            ".......",
            ".......",
            ".....Y.",
            "RRR.YY.",
        ],
        0,
        ["3"],
    ),
    _c4(
        "c4-win-vertical",
        "win",
        "Red to move: stack the fourth disc and win the column.",
        [
            ".......",
            ".......",
            ".......",
            "..R....",
            "..RY...",
            "..RYY..",
        ],
        0,
        ["2"],
    ),
    # block: Yellow threatens four next move — Red must block, not wander.
    _c4(
        "c4-block-row",
        "block",
        "Red to move: Yellow threatens the bottom row — block the open end.",
        [
            ".......",
            ".......",
            ".......",
            ".......",
            ".......",
            "RYYY.RR",
        ],
        0,
        ["4"],
    ),
    _c4(
        "c4-block-vertical",
        "block",
        "Red to move: Yellow has three stacked — cap the column.",
        [
            ".......",
            ".......",
            ".......",
            "...Y...",
            "...Y...",
            "RRRY...",
        ],
        0,
        ["3"],
    ),
    # center: on an empty board the center column is the strongest opening.
    _c4(
        "c4-center",
        "center",
        "Red to move on an empty board: take the strongest opening.",
        ["......."] * ROWS,
        0,
        ["3"],
    ),
    # double: drop between two of your discs to make an open-ended three — two ways
    # to win at once, so the opponent can only stop one (a fork).
    _c4(
        "c4-double",
        "double",
        "Red to move: create an open-ended three the opponent can't both block.",
        [
            ".......",
            ".......",
            ".......",
            ".......",
            ".......",
            "Y.R.R.Y",
        ],
        0,
        ["3"],
    ),
]


def _hd(
    id: str,
    category: str,
    description: str,
    *,
    seat: int,
    hole: list[str],
    board: list[str],
    street: str,
    stacks: list[int],
    bets: list[int],
    committed: list[int],
    solution: list[str],
) -> Challenge:
    """Build a Hold'em challenge from an explicit betting spot. The engine derives
    the observation + legal actions, so the shapes exactly match a live hand. The
    opponent's hole cards are irrelevant (never observed) — a placeholder is fine."""
    st = Holdem()
    st.pending_deal = None
    st.street = street
    st.board = list(board)
    # Placeholder opponent cards: hidden from every observation before showdown.
    st.hole = [["Xx", "Xx"], ["Xx", "Xx"]]
    st.hole[seat] = list(hole)
    st.stacks = list(stacks)
    st.bets = list(bets)
    st.committed = list(committed)
    st.to_act = seat
    st.last_raise = max(bets) - min(bets) if max(bets) > min(bets) else 2
    return Challenge(
        id=id,
        game_id="holdem",
        category=category,
        description=description,
        observation=st.observation(seat),
        legal_actions=[a.to_wire() for a in st.legal_actions(seat)],
        solution=solution,
    )


# Hold'em scenarios. Spots are chosen so one line is *objectively* right:
# 'discipline' = fold trash facing a shove, 'value' = never fold the nuts,
# 'aggression' = bet the nuts rather than check it back.
_HD_CHALLENGES: list[Challenge] = [
    _hd(
        "hd-fold-junk",
        "discipline",
        "Big blind with 7-2 offsuit: the button shoves all-in preflop — fold.",
        seat=1,
        hole=["7s", "2d"],
        board=[],
        street="preflop",
        stacks=[0, 98],
        bets=[100, 2],
        committed=[100, 2],
        solution=["fold"],
    ),
    _hd(
        "hd-fold-junk-2",
        "discipline",
        "Big blind with 8-3 offsuit: the button shoves all-in preflop — fold.",
        seat=1,
        hole=["8d", "3c"],
        board=[],
        street="preflop",
        stacks=[0, 98],
        bets=[100, 2],
        committed=[100, 2],
        solution=["fold"],
    ),
    _hd(
        "hd-call-nuts",
        "value",
        "You rivered a royal flush and the opponent shoves — never fold the nuts.",
        seat=1,
        hole=["Js", "Ts"],
        board=["As", "Ks", "Qs", "2d", "7c"],
        street="river",
        stacks=[0, 40],
        bets=[50, 0],
        committed=[60, 10],
        solution=["call"],
    ),
    _hd(
        "hd-bet-nuts",
        "aggression",
        "You rivered a royal flush and it checks to you — bet for value, any size.",
        seat=0,
        hole=["Jh", "Th"],
        board=["Ah", "Kh", "Qh", "2c", "2d"],
        street="river",
        stacks=[80, 80],
        bets=[0, 0],
        committed=[20, 20],
        solution=["raise_min", "raise_pot", "all_in"],
    ),
]

# ViZDoom Duel: the first-person frame is an opaque JPEG, so there's no
# objectively-correct *aim* to grade. What a reflex harness genuinely controls is the
# HUD read, so each scenario tests the one action that's objectively wrong for the
# spot (the solution is every legal action *except* it — same shape as Hold'em's
# multi-answer spots). 'pressure': with ammo in the tank, standing idle in a
# real-time deathmatch throws away a tick you can't get back. 'conserve': with an
# empty gun, pressing attack is a wasted press — reposition or turn to a new angle.
# The bundled `vizdoom-brawler` template passes all four.
_VD_DUEL_ACTIONS = [
    "idle",
    "attack",
    "use",
    "turn_left",
    "turn_right",
    "move_right",
    "move_left",
    "move_forward",
    "move_backward",
]


def _vd(
    id: str,
    category: str,
    description: str,
    *,
    ammo: float,
    health: float,
    tick: int,
    solution: list[str],
) -> Challenge:
    """Build a ViZDoom Duel challenge. The frame is opaque, so the observation is the
    HUD/tick a reflex harness reads (mirroring the live `vizdoom_duel` observation),
    and the solution is every action that is NOT the objectively-wrong move."""
    legal = [{"id": a, "label": a, "params": {}} for a in _VD_DUEL_ACTIONS]
    observation = {
        "game": "vizdoom_duel",
        "seat": 0,
        "frame": "",
        "hud": {"health": health, "ammo": ammo, "score": 0.0},
        "tick": tick,
        "max_ticks": 150,
        "mode": "duel",
        "legal_actions": legal,
    }
    return Challenge(
        id=id,
        game_id="vizdoom_duel",
        category=category,
        description=description,
        observation=observation,
        legal_actions=legal,
        solution=solution,
    )


_VD_NOT_IDLE = [a for a in _VD_DUEL_ACTIONS if a != "idle"]
_VD_NOT_ATTACK = [a for a in _VD_DUEL_ACTIONS if a != "attack"]

_VD_CHALLENGES: list[Challenge] = [
    # pressure: ammo in the tank — anything but standing idle.
    _vd(
        "vd-pressure-full",
        "pressure",
        "Ammo in the tank and full health: don't freeze — keep the pressure on.",
        ammo=30,
        health=100,
        tick=4,
        solution=_VD_NOT_IDLE,
    ),
    _vd(
        "vd-pressure-low",
        "pressure",
        "Still have ammo but health is low: no time to stand idle — move or fire.",
        ammo=8,
        health=25,
        tick=61,
        solution=_VD_NOT_IDLE,
    ),
    # conserve: empty gun — anything but a wasted attack.
    _vd(
        "vd-conserve-empty",
        "conserve",
        "The gun is empty: don't waste a press on attack — reposition or hunt a new angle.",
        ammo=0,
        health=100,
        tick=20,
        solution=_VD_NOT_ATTACK,
    ),
    _vd(
        "vd-conserve-empty-2",
        "conserve",
        "Out of ammo mid-fight: attacking does nothing — turn or strafe to a new line.",
        ammo=0,
        health=45,
        tick=88,
        solution=_VD_NOT_ATTACK,
    ),
]

_CHALLENGES: dict[str, list[Challenge]] = {
    "tictactoe": _TTT_CHALLENGES,
    "connect_four": _C4_CHALLENGES,
    "holdem": _HD_CHALLENGES,
    "vizdoom_duel": _VD_CHALLENGES,
}


def scenarios_for(game_id: str) -> list[dict[str, Any]]:
    """The public scenarios (no solutions) the node runs its harness against."""
    return [c.public() for c in _CHALLENGES.get(game_id, [])]


def grade(game_id: str, answers: dict[str, str]) -> dict[str, Any]:
    """Grade a node's `{scenario_id: action_id}` answers against the hidden solutions.

    Returns an overall score plus a per-category breakdown and how many categories the
    harness covered (got at least one right in)."""
    challenges = _CHALLENGES.get(game_id, [])
    categories: dict[str, dict[str, int]] = {}
    correct = 0
    for ch in challenges:
        cat = categories.setdefault(ch.category, {"passed": 0, "total": 0})
        cat["total"] += 1
        if str(answers.get(ch.id, "")) in ch.solution:
            cat["passed"] += 1
            correct += 1
    total = len(challenges)
    covered = sum(1 for c in categories.values() if c["passed"] > 0)
    return {
        "correct": correct,
        "total": total,
        "score": round(correct / total, 3) if total else 0.0,
        "categories": categories,
        "covered": covered,
        "category_count": len(categories),
    }
