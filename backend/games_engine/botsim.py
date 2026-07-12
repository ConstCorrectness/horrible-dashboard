"""Arena bot simulation: run two player-written bots head-to-head on a small
deterministic grid, each bot isolated in its own subprocess.

The arena is a 9x9 **resource duel**: two bots start in opposite corners; pellets
are scattered by the round's seed; each tick a bot sees the board and returns a
move (``up``/``down``/``left``/``right``/``stay``). Stepping onto a pellet scores
it; stepping onto the opponent from behind (their tail cell) steals two of their
points. Most points after ``TICKS`` wins the round.

Each bot runs as ``python -I bot_host.py`` (isolated, empty env, temp cwd, kill
tree, POSIX rlimits — the same posture as ``games_engine/verify.py``, and
``GAMES_ENABLE_CODE_EXEC``-gated). The parent writes one JSON observation line
per tick and reads one action line; a missed deadline is ``stay``; a crash
forfeits the round. Synchronous ``Popen`` by design (Windows + reload safety);
async callers run it in a thread.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from backend.games_engine import verify

GRID = 9
TICKS = 100
PELLETS = 14
TICK_BUDGET_S = 0.1
STEAL = 2

# The child harness: defines nothing itself — it imports the player's `bot(obs)`
# from bot.py and pumps stdin→stdout one tick at a time.
_BOT_HOST = """\
import json, os, sys

# `python -I` drops the script dir from sys.path — add it back so `import bot` works.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import bot as _b
    _fn = getattr(_b, "bot", None)
except Exception:
    _fn = None

while True:
    # Explicit readline (NOT `for line in sys.stdin`, whose read-ahead buffering
    # would block until a big chunk or EOF instead of yielding per tick).
    _line = sys.stdin.readline()
    if not _line:
        break
    _line = _line.strip()
    if not _line:
        continue
    try:
        _obs = json.loads(_line)
    except Exception:
        print("stay", flush=True)
        continue
    try:
        _mv = _fn(_obs) if _fn else "stay"
    except Exception:
        _mv = "stay"
    if _mv not in ("up", "down", "left", "right", "stay"):
        _mv = "stay"
    print(_mv, flush=True)
"""

_MOVES = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
    "stay": (0, 0),
}


@dataclass
class _Bot:
    proc: subprocess.Popen | None
    alive: bool = True
    x: int = 0
    y: int = 0
    score: int = 0
    prev: tuple[int, int] = (0, 0)


@dataclass
class ArenaResult:
    scores: list[int]
    winner: int | None
    ticks: list[dict[str, Any]] = field(default_factory=list)  # per-tick log for replay
    forfeits: list[bool] = field(default_factory=lambda: [False, False])


def _spawn(code: str):
    if os.name != "nt":

        def limits() -> None:
            import resource

            os.setsid()
            resource.setrlimit(resource.RLIMIT_CPU, (10, 12))
            mem = 256 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))

        preexec = limits
    else:
        preexec = None
    env: dict[str, str] = {}
    if os.name == "nt":
        for var in ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC"):
            if os.environ.get(var):
                env[var] = os.environ[var]
    import tempfile
    from pathlib import Path

    tmp = tempfile.mkdtemp(prefix="arena-bot-")
    # Fully write + close before the child starts, or it imports an empty bot.py.
    Path(tmp, "bot.py").write_text(code or "", encoding="utf-8")
    Path(tmp, "bot_host.py").write_text(_BOT_HOST, encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": tmp,
        "env": env,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "text": True,
        "bufsize": 1,
    }
    if preexec is not None:
        kwargs["preexec_fn"] = preexec
    return subprocess.Popen([sys.executable, "-I", "bot_host.py"], **kwargs)


def _kill(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _ask(bot: _Bot, obs: dict[str, Any]) -> str:
    """One tick's move from a bot, or 'stay' on any failure (and mark it dead)."""
    if (
        not bot.alive
        or bot.proc is None
        or bot.proc.stdin is None
        or bot.proc.stdout is None
    ):
        return "stay"
    try:
        bot.proc.stdin.write(json.dumps(obs) + "\n")
        bot.proc.stdin.flush()
        # A blocking readline; the CPU rlimit / kill on teardown bounds a hang on
        # POSIX. On Windows we rely on the child's own cooperation + teardown kill.
        line = bot.proc.stdout.readline()
        if not line:
            bot.alive = False
            return "stay"
        return line.strip() or "stay"
    except Exception:
        bot.alive = False
        return "stay"


def simulate(code_a: str, code_b: str, seed: int) -> ArenaResult:
    """Run one arena round. Requires the code-exec gate; returns a draw with a
    forfeit flag when disabled so a table still completes."""
    if not verify.code_exec_enabled():
        return ArenaResult(scores=[0, 0], winner=None, forfeits=[True, True])
    rng = random.Random(seed)
    pellets = set()
    while len(pellets) < PELLETS:
        p = (rng.randrange(GRID), rng.randrange(GRID))
        if p not in ((0, 0), (GRID - 1, GRID - 1)):
            pellets.add(p)

    bots = [
        _Bot(proc=_spawn(code_a), x=0, y=0, prev=(0, 0)),
        _Bot(proc=_spawn(code_b), x=GRID - 1, y=GRID - 1, prev=(GRID - 1, GRID - 1)),
    ]
    ticks: list[dict[str, Any]] = []
    try:
        for _ in range(TICKS):
            for i, bot in enumerate(bots):
                other = bots[1 - i]
                obs = {
                    "me": [bot.x, bot.y],
                    "opponent": [other.x, other.y],
                    "opponent_prev": list(other.prev),
                    "pellets": sorted(list(pellets)),
                    "grid": GRID,
                    "my_score": bot.score,
                    "opponent_score": other.score,
                }
                move = _ask(bot, obs)
                dx, dy = _MOVES.get(move, (0, 0))
                nx, ny = bot.x + dx, bot.y + dy
                if 0 <= nx < GRID and 0 <= ny < GRID:
                    bot.prev = (bot.x, bot.y)
                    bot.x, bot.y = nx, ny
                if (bot.x, bot.y) in pellets:
                    pellets.discard((bot.x, bot.y))
                    bot.score += 1
                # Steal: stepping onto the opponent's just-vacated cell (behind them).
                if (bot.x, bot.y) == other.prev and other.score > 0:
                    stolen = min(STEAL, other.score)
                    other.score -= stolen
                    bot.score += stolen
            ticks.append(
                {
                    "p": [[b.x, b.y] for b in bots],
                    "s": [b.score for b in bots],
                    "pellets": sorted(list(pellets)),
                }
            )
            if not pellets:
                break
    finally:
        for bot in bots:
            _kill(bot.proc)

    forfeits = [not b.alive for b in bots]
    scores = [b.score for b in bots]
    # A forfeiting bot loses the round outright.
    if forfeits[0] and not forfeits[1]:
        winner: int | None = 1
    elif forfeits[1] and not forfeits[0]:
        winner = 0
    elif scores[0] == scores[1]:
        winner = None
    else:
        winner = 0 if scores[0] > scores[1] else 1
    return ArenaResult(scores=scores, winner=winner, ticks=ticks, forfeits=forfeits)
