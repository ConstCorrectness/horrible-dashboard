"""Code golf duel: same spec, hidden tests — correctness first, brevity breaks ties.

Both seats get the same prompt + function signature + **public examples** in one
simultaneous turn and submit a `solution.py` as an open action (`payload:
"code"`). When both are in, the state flips to `WORK`: the server grades each
submission against the task's **hidden pytest suite** (`games_engine/verify.py`,
gated by `GAMES_ENABLE_CODE_EXEC`). A submission that passes everything is
*correct*; among correct submissions the **shorter byte count** wins.

Anti-cheat is the challenge-track shape: hidden tests never go over the wire;
`public_state()` reveals both solutions and the per-seat test report only after
the game (learning > secrecy once it's over). Each seat's observation also
carries `starter_code` — a working-but-verbose reference the baseline solver
submits, so a fresh node always finishes with a *correct* entry and loses on
bytes; beating the starter is the floor of the skill curve.
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
        "id": "golf-runlength",
        "prompt": (
            "Write run-length encoding: encode('aaabcc') == 'a3b1c2'. Empty "
            "string encodes to ''."
        ),
        "signature": "def encode(s: str) -> str",
        "public_examples": ["encode('aaabcc') == 'a3b1c2'", "encode('') == ''"],
        "starter_code": (
            "def encode(s: str) -> str:\n"
            "    result = ''\n"
            "    index = 0\n"
            "    while index < len(s):\n"
            "        char = s[index]\n"
            "        count = 0\n"
            "        while index < len(s) and s[index] == char:\n"
            "            count = count + 1\n"
            "            index = index + 1\n"
            "        result = result + char + str(count)\n"
            "    return result\n"
        ),
        "hidden_tests": (
            "from solution import encode\n\n"
            "def test_basic():\n    assert encode('aaabcc') == 'a3b1c2'\n\n"
            "def test_empty():\n    assert encode('') == ''\n\n"
            "def test_single():\n    assert encode('z') == 'z1'\n\n"
            "def test_alternating():\n    assert encode('ababab') == 'a1b1a1b1a1b1'\n\n"
            "def test_long_run():\n    assert encode('a' * 12) == 'a12'\n"
        ),
    },
    {
        "id": "golf-brackets",
        "prompt": (
            "Write balanced(s): True iff every (, [, { closes in order. Other "
            "characters are ignored."
        ),
        "signature": "def balanced(s: str) -> bool",
        "public_examples": [
            "balanced('a(b[c]{d})') is True",
            "balanced('(]') is False",
        ],
        "starter_code": (
            "def balanced(s: str) -> bool:\n"
            "    stack = []\n"
            "    pairs = {')': '(', ']': '[', '}': '{'}\n"
            "    for character in s:\n"
            "        if character in '([{':\n"
            "            stack.append(character)\n"
            "        elif character in pairs:\n"
            "            if not stack or stack.pop() != pairs[character]:\n"
            "                return False\n"
            "    return not stack\n"
        ),
        "hidden_tests": (
            "from solution import balanced\n\n"
            "def test_ok():\n    assert balanced('a(b[c]{d})') is True\n\n"
            "def test_cross():\n    assert balanced('([)]') is False\n\n"
            "def test_open():\n    assert balanced('(((') is False\n\n"
            "def test_close():\n    assert balanced(')') is False\n\n"
            "def test_empty():\n    assert balanced('') is True\n"
        ),
    },
    {
        "id": "golf-digits",
        "prompt": (
            "Write persistence(n): how many times you must multiply the digits "
            "of a non-negative int together until a single digit remains. "
            "persistence(39) == 3 (39→27→14→4)."
        ),
        "signature": "def persistence(n: int) -> int",
        "public_examples": ["persistence(39) == 3", "persistence(4) == 0"],
        "starter_code": (
            "def persistence(n: int) -> int:\n"
            "    steps = 0\n"
            "    while n >= 10:\n"
            "        product = 1\n"
            "        for digit in str(n):\n"
            "            product = product * int(digit)\n"
            "        n = product\n"
            "        steps = steps + 1\n"
            "    return steps\n"
        ),
        "hidden_tests": (
            "from solution import persistence\n\n"
            "def test_39():\n    assert persistence(39) == 3\n\n"
            "def test_single():\n    assert persistence(4) == 0\n\n"
            "def test_999():\n    assert persistence(999) == 4\n\n"
            "def test_10():\n    assert persistence(10) == 1\n"
        ),
    },
]


class CodeGolf(GameState):
    def __init__(self, task: dict[str, Any] | None = None) -> None:
        self.task = task or _random.choice(DEFAULT_TASKS)
        self.submissions: list[str | None] = [None, None]
        # Per-seat grading report, filled by run_work: {green, passed, failed, out}.
        self.reports: list[dict[str, Any]] | None = None

    # ---- turn structure ----------------------------------------------------

    def current_players(self) -> list[int]:
        return [s for s in (0, 1) if self.submissions[s] is None]

    def current_player(self) -> int:
        if self.current_players():
            return self.current_players()[0]
        return TERMINAL if self.reports is not None else WORK

    def legal_actions(self, player: int) -> list[Action]:
        if player not in self.current_players():
            return []
        return [
            Action(
                id="submit",
                label="submit your solution.py",
                params={"payload": "code", "max_code_chars": MAX_CODE_CHARS},
            )
        ]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player not in self.current_players():
            raise ValueError("this seat has already submitted")
        if action_id != "submit":
            raise ValueError(f"bad action id {action_id!r}")
        code = payload.get("code") if isinstance(payload, dict) else payload
        self.submissions[player] = str(code or "")[:MAX_CODE_CHARS]

    def run_work(self) -> None:
        """Grade both submissions against the hidden suite (blocking; the referee
        runs this off-loop)."""
        reports = []
        for seat in (0, 1):
            result = verify.run_python_job(
                {
                    "solution.py": self.submissions[seat] or "",
                    "test_hidden.py": str(self.task["hidden_tests"]),
                }
            )
            reports.append(
                {
                    "green": result.green,
                    "passed": result.passed,
                    "failed": result.failed,
                    "bytes": len((self.submissions[seat] or "").encode("utf-8")),
                    "output": (result.stdout + result.stderr)[-2000:],
                }
            )
        self.reports = reports

    # ---- views -------------------------------------------------------------

    def observation(self, player: int) -> dict[str, Any]:
        return {
            "game": "code_golf",
            "seat": player,
            "prompt": self.task["prompt"],
            "signature": self.task["signature"],
            "public_examples": list(self.task["public_examples"]),
            "starter_code": self.task.get("starter_code", ""),
            "scoring": "correctness first, fewest bytes wins ties",
            "submitted": [s is not None for s in self.submissions],
        }

    def public_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "game": "code_golf",
            "task_id": self.task["id"],
            "prompt": self.task["prompt"],
            "signature": self.task["signature"],
            "submitted": [s is not None for s in self.submissions],
            "grading": self.current_player() == WORK,
            "turn": None,
            "winner": self._winner() if self.is_terminal() else None,
        }
        if self.is_terminal() and self.reports is not None:
            # Post-game reveal: both solutions + reports (learning > secrecy).
            state["reports"] = self.reports
            state["solutions"] = [s or "" for s in self.submissions]
        return state

    # ---- outcome -----------------------------------------------------------

    def _winner(self) -> int | None:
        if self.reports is None:
            return None
        g0, g1 = self.reports[0]["green"], self.reports[1]["green"]
        if g0 and not g1:
            return 0
        if g1 and not g0:
            return 1
        if not g0 and not g1:
            return None
        b0, b1 = self.reports[0]["bytes"], self.reports[1]["bytes"]
        if b0 == b1:
            return None
        return 0 if b0 < b1 else 1

    def returns(self) -> dict[int, float]:
        w = self._winner()
        if w is None:
            return {0: 0.0, 1: 0.0}
        return {w: 1.0, 1 - w: -1.0}


SPEC = register_game(
    GameSpec(
        id="code_golf",
        name="Code Golf",
        min_players=2,
        max_players=2,
        factory=CodeGolf,
        move_timeout_s=MOVE_TIMEOUT_S,
        decision_class="reasoner",
        default_policy="agent",
    )
)
