"""Test duel: adversarial test-writing — spec-lawyering as a game.

Two sequential simultaneous phases against the same written spec:

1. **impl** — both seats submit an implementation (`solution.py`).
2. **tests** — both seats submit a pytest suite aimed at the spec, trying to
   break the *opponent's* implementation.

Then `WORK` grades (server-side, hidden reference implementation):

- your suite is **valid** if it passes against the reference implementation
  (a test the spec's own reference fails is an invalid test — it costs you);
- your suite **kills** if it's valid and the opponent's implementation fails it;
- your implementation **holds** if it passes the task's hidden reference tests.

Score per seat = 2·holds + 2·kills + 1·valid; `returns()` is the zero-sum delta.
The post-game `public_state` reveals all four artifacts + the grading report —
the whole point is studying how your opponent lawyered the spec.
"""

from __future__ import annotations

import random as _random
from typing import Any

from backend.games_engine import verify
from backend.games_engine.base import (
    TERMINAL,
    WORK,
    Action,
    GameSpec,
    GameState,
    register_game,
)

MOVE_TIMEOUT_S = 600.0
MAX_CODE_CHARS = 20_000

DEFAULT_TASKS: list[dict[str, Any]] = [
    {
        "id": "duel-slugify",
        "spec": (
            "Implement slugify(title: str) -> str. Rules: lowercase; runs of "
            "non-alphanumeric characters become a single '-'; no leading or "
            "trailing '-'; the empty string (or all-symbols input) returns ''."
        ),
        "signature": "def slugify(title: str) -> str",
        "reference_impl": (
            "import re\n\n"
            "def slugify(title: str) -> str:\n"
            "    return re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')\n"
        ),
        "reference_tests": (
            "from solution import slugify\n\n"
            "def test_basic():\n    assert slugify('Hello, World!') == 'hello-world'\n\n"
            "def test_runs():\n    assert slugify('a  --  b') == 'a-b'\n\n"
            "def test_edges():\n    assert slugify('***') == ''\n\n"
            "def test_empty():\n    assert slugify('') == ''\n"
        ),
    },
    {
        "id": "duel-median",
        "spec": (
            "Implement median(xs: list[float]) -> float. The median of a sorted "
            "copy; for even lengths, the mean of the two middle values. Raise "
            "ValueError on an empty list. Must not mutate the input."
        ),
        "signature": "def median(xs: list[float]) -> float",
        "reference_impl": (
            "def median(xs: list[float]) -> float:\n"
            "    if not xs:\n"
            "        raise ValueError('empty')\n"
            "    s = sorted(xs)\n"
            "    n = len(s)\n"
            "    mid = n // 2\n"
            "    if n % 2:\n"
            "        return float(s[mid])\n"
            "    return (s[mid - 1] + s[mid]) / 2\n"
        ),
        "reference_tests": (
            "import pytest\nfrom solution import median\n\n"
            "def test_odd():\n    assert median([3, 1, 2]) == 2\n\n"
            "def test_even():\n    assert median([4, 1, 3, 2]) == 2.5\n\n"
            "def test_empty():\n"
            "    with pytest.raises(ValueError):\n        median([])\n\n"
            "def test_no_mutation():\n"
            "    xs = [3, 1, 2]\n    median(xs)\n    assert xs == [3, 1, 2]\n"
        ),
    },
]

# Grading weights: holding up > killing > merely writing valid tests.
POINTS_HOLDS = 2
POINTS_KILLS = 2
POINTS_VALID = 1


class TestDuel(GameState):
    def __init__(self, task: dict[str, Any] | None = None) -> None:
        self.task = task or _random.choice(DEFAULT_TASKS)
        self.phase = "impl"  # impl -> tests -> (WORK) -> done
        self.impls: list[str | None] = [None, None]
        self.tests: list[str | None] = [None, None]
        self.reports: list[dict[str, Any]] | None = None

    # ---- turn structure ----------------------------------------------------

    def _pending(self) -> list[str | None]:
        return self.impls if self.phase == "impl" else self.tests

    def current_players(self) -> list[int]:
        if self.phase in ("impl", "tests"):
            return [s for s in (0, 1) if self._pending()[s] is None]
        return []

    def current_player(self) -> int:
        pending = self.current_players()
        if pending:
            return pending[0]
        return TERMINAL if self.reports is not None else WORK

    def legal_actions(self, player: int) -> list[Action]:
        if player not in self.current_players():
            return []
        if self.phase == "impl":
            return [
                Action(
                    id="submit_impl",
                    label="submit your implementation (solution.py)",
                    params={"payload": "code", "max_code_chars": MAX_CODE_CHARS},
                )
            ]
        return [
            Action(
                id="submit_tests",
                label="submit your pytest suite (targets solution.py)",
                params={"payload": "code", "max_code_chars": MAX_CODE_CHARS},
            )
        ]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player not in self.current_players():
            raise ValueError("this seat has already submitted")
        expected = "submit_impl" if self.phase == "impl" else "submit_tests"
        if action_id != expected:
            raise ValueError(f"bad action id {action_id!r} (phase: {self.phase})")
        code = payload.get("code") if isinstance(payload, dict) else payload
        self._pending()[player] = str(code or "")[:MAX_CODE_CHARS]
        # The last implementation in flips the game to the test-writing phase —
        # the referee re-prompts both seats with the new legal action.
        if self.phase == "impl" and all(i is not None for i in self.impls):
            self.phase = "tests"

    def run_work(self) -> None:
        """Six graded jobs (blocking; the referee runs this off-loop): each impl vs
        the hidden reference tests, and each suite vs reference + opponent impls."""
        reports: list[dict[str, Any]] = []
        for seat in (0, 1):
            impl = self.impls[seat] or ""
            suite = self.tests[seat] or ""
            holds = verify.run_python_job(
                {"solution.py": impl, "test_ref.py": str(self.task["reference_tests"])}
            ).green
            valid = (
                bool(suite)
                and verify.run_python_job(
                    {
                        "solution.py": str(self.task["reference_impl"]),
                        "test_user.py": suite,
                    }
                ).green
            )
            opponent_run = (
                verify.run_python_job(
                    {"solution.py": self.impls[1 - seat] or "", "test_user.py": suite}
                )
                if valid
                else None
            )
            kills = bool(valid and opponent_run is not None and not opponent_run.green)
            reports.append(
                {
                    "holds": holds,
                    "valid_tests": valid,
                    "kills": kills,
                    "score": POINTS_HOLDS * holds
                    + POINTS_KILLS * kills
                    + POINTS_VALID * valid,
                }
            )
        self.reports = reports

    # ---- views -------------------------------------------------------------

    def observation(self, player: int) -> dict[str, Any]:
        return {
            "game": "test_duel",
            "seat": player,
            "phase": self.phase,
            "spec": self.task["spec"],
            "signature": self.task["signature"],
            "scoring": (
                "2 pts if your impl passes the hidden reference tests; 2 pts if "
                "your (valid) tests break the opponent's impl; 1 pt for a valid "
                "suite. Invalid tests (failing the reference impl) score nothing."
            ),
            "submitted": [s is not None for s in self._pending()],
        }

    def public_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "game": "test_duel",
            "task_id": self.task["id"],
            "spec": self.task["spec"],
            "phase": self.phase,
            "submitted_impls": [s is not None for s in self.impls],
            "submitted_tests": [s is not None for s in self.tests],
            "grading": self.current_player() == WORK,
            "turn": None,
            "winner": self._winner() if self.is_terminal() else None,
        }
        if self.is_terminal() and self.reports is not None:
            state["reports"] = self.reports
            state["impls"] = [s or "" for s in self.impls]
            state["tests"] = [s or "" for s in self.tests]
        return state

    # ---- outcome -----------------------------------------------------------

    def _winner(self) -> int | None:
        if self.reports is None:
            return None
        s0, s1 = self.reports[0]["score"], self.reports[1]["score"]
        if s0 == s1:
            return None
        return 0 if s0 > s1 else 1

    def returns(self) -> dict[int, float]:
        if self.reports is None:
            return {0: 0.0, 1: 0.0}
        s0, s1 = self.reports[0]["score"], self.reports[1]["score"]
        return {0: float(s0 - s1), 1: float(s1 - s0)}


SPEC = register_game(
    GameSpec(
        id="test_duel",
        name="Test Duel",
        min_players=2,
        max_players=2,
        factory=TestDuel,
        move_timeout_s=MOVE_TIMEOUT_S,
        decision_class="reasoner",
        default_policy="agent",
    )
)
