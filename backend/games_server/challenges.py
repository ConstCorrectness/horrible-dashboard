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

from backend.games_engine.tictactoe import TicTacToe

# 'X' = seat 0, 'O' = seat 1, '.' = empty.
_SEAT = {"X": 0, "O": 1}


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

_CHALLENGES: dict[str, list[Challenge]] = {"tictactoe": _TTT_CHALLENGES}


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
