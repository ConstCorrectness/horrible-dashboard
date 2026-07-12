"""Bug-hunt race: competitive SWE-bench — same broken repo, first to green wins.

Both seats get the same task (description + buggy files + **visible tests**) in
one long simultaneous window and submit whole-file fixes as an open action
(`payload: "files"`). Every submission flips the state to `WORK`: the server
re-runs the visible **and hidden** test suites (`games_engine/verify.py`).

- **Green** (everything passes) → the submitter wins on the spot. Grading is
  serialized under the referee's lock, so "first to green" means first to
  *verified* green — grading time is part of the race.
- **Red** → the attempt (with its test output) lands in that seat's observation
  and the seat goes back on the clock: the feedback loop is the game.
- A seat that exhausts `MAX_ATTEMPTS` (timeouts count — the move clock auto-
  submits) is done; when both are done, the better best-attempt (most tests
  passed) takes it, else a draw.

Hidden tests are the anti-memorization margin: passing the visible suite while
failing the hidden one is not green. Tasks come from the server's task bank
(`task_bank.pick_task` hands each match a task neither player has seen).
"""

from __future__ import annotations

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

MOVE_TIMEOUT_S = 900.0
MAX_ATTEMPTS = 8
MAX_FILE_CHARS = 40_000
MAX_FILES = 12

# A tiny fallback task so a table can start with no bank wired (tests, local dev).
DEFAULT_TASK: dict[str, Any] = {
    "id": "bh-default-offby",
    "description": "count_evens returns the wrong count — find and fix the bug.",
    "files": {
        "counting.py": (
            "def count_evens(numbers):\n"
            "    count = 0\n"
            "    for n in numbers:\n"
            "        if n % 2 == 1:\n"
            "            count += 1\n"
            "    return count\n"
        )
    },
    "visible_tests": {
        "test_counting.py": (
            "from counting import count_evens\n\n"
            "def test_basic():\n    assert count_evens([1, 2, 3, 4]) == 2\n"
        )
    },
    "hidden_tests": {
        "test_hidden_counting.py": (
            "from counting import count_evens\n\n"
            "def test_empty():\n    assert count_evens([]) == 0\n\n"
            "def test_all_even():\n    assert count_evens([2, 4, 6]) == 3\n"
        )
    },
}


class BugHunt(GameState):
    def __init__(self, task: dict[str, Any] | None = None) -> None:
        self.task = task or DEFAULT_TASK
        # Per-seat attempt history: {passed, failed, green, output}.
        self.attempts: list[list[dict[str, Any]]] = [[], []]
        self.solved: int | None = None  # the winning seat, once someone greens
        # A submission awaiting grading: (seat, files). One at a time — the
        # referee's lock serializes actions and grading.
        self._pending: tuple[int, dict[str, str]] | None = None

    # ---- turn structure ----------------------------------------------------

    def _seat_done(self, seat: int) -> bool:
        return self.solved is not None or len(self.attempts[seat]) >= MAX_ATTEMPTS

    def current_players(self) -> list[int]:
        if self.solved is not None or self._pending is not None:
            return []
        return [s for s in (0, 1) if not self._seat_done(s)]

    def current_player(self) -> int:
        if self._pending is not None:
            return WORK
        if self.solved is not None or all(self._seat_done(s) for s in (0, 1)):
            return TERMINAL
        return self.current_players()[0]

    def legal_actions(self, player: int) -> list[Action]:
        if player not in self.current_players():
            return []
        return [
            Action(
                id="submit",
                label="submit your fixed files",
                params={
                    "payload": "files",
                    "max_files": MAX_FILES,
                    "max_file_chars": MAX_FILE_CHARS,
                },
            )
        ]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player not in self.current_players():
            raise ValueError("this seat may not submit right now")
        if action_id != "submit":
            raise ValueError(f"bad action id {action_id!r}")
        self._pending = (player, self._clean_files(payload))

    def _clean_files(self, payload: Any) -> dict[str, str]:
        """Whole-file replacements, restricted to the task's own file paths."""
        raw = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return dict(self.task["files"])  # empty submission = unchanged repo
        allowed = set(self.task["files"])
        files = dict(self.task["files"])
        for name, content in list(raw.items())[:MAX_FILES]:
            if str(name) in allowed:
                files[str(name)] = str(content)[:MAX_FILE_CHARS]
        return files

    def run_work(self) -> None:
        """Grade the pending submission against visible + hidden suites."""
        assert self._pending is not None
        seat, files = self._pending
        result = verify.run_python_job(
            {
                **files,
                **{str(k): str(v) for k, v in self.task["visible_tests"].items()},
                **{str(k): str(v) for k, v in self.task["hidden_tests"].items()},
            }
        )
        self.attempts[seat].append(
            {
                "green": result.green,
                "passed": result.passed,
                "failed": result.failed,
                "output": (result.stdout + result.stderr)[-3000:],
                "files": files if result.green else None,
            }
        )
        self._pending = None
        if result.green:
            self.solved = seat

    # ---- views -------------------------------------------------------------

    def observation(self, player: int) -> dict[str, Any]:
        return {
            "game": "bug_hunt",
            "seat": player,
            "description": self.task["description"],
            "files": dict(self.task["files"]),
            "visible_tests": dict(self.task["visible_tests"]),
            "attempts": [
                # Own attempt feedback only — the opponent's diagnostics are theirs.
                {k: v for k, v in attempt.items() if k != "files"}
                for attempt in self.attempts[player]
            ],
            "attempts_left": MAX_ATTEMPTS - len(self.attempts[player]),
            "opponent_attempts": len(self.attempts[1 - player]),
        }

    def public_state(self) -> dict[str, Any]:
        done = self.is_terminal()
        state: dict[str, Any] = {
            "game": "bug_hunt",
            "task_id": self.task["id"],
            "description": self.task["description"],
            "grading": self._pending is not None,
            "attempts": [
                [
                    {"green": a["green"], "passed": a["passed"], "failed": a["failed"]}
                    for a in self.attempts[s]
                ]
                for s in (0, 1)
            ],
            "turn": None,
            "winner": self._winner() if done else None,
        }
        if done:
            # Post-race reveal: the broken repo and (if someone won) the fix diff
            # source — plus the hidden tests, for learning.
            state["files"] = dict(self.task["files"])
            state["hidden_tests"] = dict(self.task["hidden_tests"])
            if self.solved is not None and self.attempts[self.solved]:
                state["winning_files"] = self.attempts[self.solved][-1]["files"]
        return state

    # ---- outcome -----------------------------------------------------------

    def _best_passed(self, seat: int) -> int:
        return max((a["passed"] for a in self.attempts[seat]), default=0)

    def _winner(self) -> int | None:
        if self.solved is not None:
            return self.solved
        b0, b1 = self._best_passed(0), self._best_passed(1)
        if b0 == b1:
            return None
        return 0 if b0 > b1 else 1

    def returns(self) -> dict[int, float]:
        w = self._winner()
        if w is None:
            return {0: 0.0, 1: 0.0}
        return {w: 1.0, 1 - w: -1.0}


SPEC = register_game(
    GameSpec(
        id="bug_hunt",
        name="Bug Hunt",
        min_players=2,
        max_players=2,
        factory=BugHunt,
        move_timeout_s=MOVE_TIMEOUT_S,
    )
)
