"""Fresh bug-hunt tasks by planting bugs in known-good mini-repos.

Given a *seed* (a correct module + a full test suite), we apply an AST mutation
(flip a comparison, off-by-one a constant, swap a boolean op, drop a statement),
then accept the mutant only if the invariants hold:

    seed green  →  mutant red on ≥1 visible AND ≥1 hidden test  →  reverting greens

So every generated task is a *real* single-bug defect whose fix restores all
tests — no degenerate or unfixable mutants. Run offline/on a cron:

    python -m backend.games_server.task_gen --count 20

Requires ``GAMES_ENABLE_CODE_EXEC=1`` (it runs the seed + mutant to verify).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import random
from typing import Any

from backend.games_engine import verify
from backend.games_server import task_bank

# Seeds: a correct module, a visible suite, and a hidden suite. Kept tiny and
# self-contained so mutation + verification is fast.
SEEDS: list[dict[str, Any]] = [
    {
        "id": "seed-stats",
        "description": "A small statistics helper module.",
        "module_name": "stats.py",
        "module": (
            "def mean(xs):\n"
            "    return sum(xs) / len(xs)\n\n\n"
            "def clamp(x, lo, hi):\n"
            "    if x < lo:\n"
            "        return lo\n"
            "    if x > hi:\n"
            "        return hi\n"
            "    return x\n\n\n"
            "def running_max(xs):\n"
            "    best = xs[0]\n"
            "    out = []\n"
            "    for x in xs:\n"
            "        if x > best:\n"
            "            best = x\n"
            "        out.append(best)\n"
            "    return out\n"
        ),
        "visible": (
            "from stats import mean, clamp, running_max\n\n"
            "def test_mean():\n    assert mean([1, 2, 3]) == 2\n\n"
            "def test_clamp():\n    assert clamp(5, 0, 3) == 3\n    assert clamp(-1, 0, 3) == 0\n\n"
            "def test_running_max():\n    assert running_max([1, 3, 2, 5]) == [1, 3, 3, 5]\n"
        ),
        "hidden": (
            "from stats import clamp, running_max\n\n"
            "def test_clamp_inside():\n    assert clamp(2, 0, 3) == 2\n\n"
            "def test_running_max_flat():\n    assert running_max([4, 4, 4]) == [4, 4, 4]\n"
        ),
    },
    {
        "id": "seed-text",
        "description": "A small text-processing module.",
        "module_name": "textutil.py",
        "module": (
            "def word_count(text):\n"
            "    return len(text.split())\n\n\n"
            "def is_palindrome(s):\n"
            "    s = s.lower()\n"
            "    return s == s[::-1]\n\n\n"
            "def truncate(s, n):\n"
            "    if len(s) <= n:\n"
            "        return s\n"
            "    return s[:n] + '...'\n"
        ),
        "visible": (
            "from textutil import word_count, is_palindrome, truncate\n\n"
            "def test_words():\n    assert word_count('a b c') == 3\n\n"
            "def test_palindrome():\n    assert is_palindrome('Racecar')\n    assert not is_palindrome('abc')\n\n"
            "def test_truncate():\n    assert truncate('hello', 3) == 'hel...'\n"
        ),
        "hidden": (
            "from textutil import truncate, word_count\n\n"
            "def test_truncate_exact():\n    assert truncate('hey', 3) == 'hey'\n\n"
            "def test_words_empty():\n    assert word_count('') == 0\n"
        ),
    },
]


class _Mutator(ast.NodeTransformer):
    """Apply exactly one mutation, chosen at random from the eligible sites."""

    _CMP_FLIP = {
        ast.Lt: ast.Gt,
        ast.Gt: ast.Lt,
        ast.LtE: ast.GtE,
        ast.GtE: ast.LtE,
        ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq,
    }

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._sites: list[Any] = []

    def collect(self, tree: ast.AST) -> int:
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and type(node.ops[0]) in self._CMP_FLIP:
                self._sites.append(("cmp", node))
            elif isinstance(node, ast.Constant) and isinstance(node.value, int):
                self._sites.append(("int", node))
            elif isinstance(node, ast.BoolOp):
                self._sites.append(("bool", node))
        return len(self._sites)

    def mutate(self, tree: ast.AST) -> ast.AST:
        kind, node = self._rng.choice(self._sites)
        if kind == "cmp":
            node.ops[0] = self._CMP_FLIP[type(node.ops[0])]()
        elif kind == "int":
            node.value = node.value + self._rng.choice([-1, 1])
        elif kind == "bool":
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return ast.fix_missing_locations(tree)


def _run(module_name: str, module: str, tests: dict[str, str]) -> bool:
    return verify.run_python_job({module_name: module, **tests}).green


def generate_one(seed: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
    """One accepted mutant task, or None if this attempt didn't yield a valid bug."""
    visible = {"test_visible.py": seed["visible"]}
    hidden = {"test_hidden.py": seed["hidden"]}
    # Seed must be green to start.
    if not _run(seed["module_name"], seed["module"], {**visible, **hidden}):
        return None
    tree = ast.parse(seed["module"])
    mutator = _Mutator(rng)
    if mutator.collect(tree) == 0:
        return None
    mutated = ast.unparse(mutator.mutate(tree))
    if mutated == seed["module"]:
        return None
    # Mutant must break both a visible and a hidden test (a real, visible-to-the-
    # player defect that the hidden suite also catches).
    if _run(seed["module_name"], mutated, visible):
        return None
    if _run(seed["module_name"], mutated, hidden):
        return None
    # Reverting (the original module) must restore green — proves it's fixable.
    if not _run(seed["module_name"], seed["module"], {**visible, **hidden}):
        return None
    digest = hashlib.sha1(mutated.encode()).hexdigest()[:8]
    return {
        "id": f"gen-{seed['id']}-{digest}",
        "kind": "bug_hunt",
        "difficulty": "standard",
        "payload": {
            "description": seed["description"] + " One function has a bug.",
            "files": {seed["module_name"]: mutated},
            "visible_tests": visible,
        },
        "hidden": {"hidden_tests": hidden},
    }


def generate(count: int, seed_value: int | None = None) -> list[str]:
    """Generate up to `count` fresh tasks into the bank; returns the ids added."""
    if not verify.code_exec_enabled():
        raise RuntimeError("set GAMES_ENABLE_CODE_EXEC=1 to generate tasks")
    rng = random.Random(seed_value)
    added: list[str] = []
    attempts = 0
    while len(added) < count and attempts < count * 20:
        attempts += 1
        seed = rng.choice(SEEDS)
        task = generate_one(seed, rng)
        if task is None:
            continue
        task_bank.add_task(
            task["id"],
            task["kind"],
            task["difficulty"],
            task["payload"],
            task["hidden"],
            source="generated",
        )
        added.append(task["id"])
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate bug-hunt tasks.")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    added = generate(args.count, args.seed)
    print(f"added {len(added)} tasks: {', '.join(added) or '(none)'}")


if __name__ == "__main__":
    main()
