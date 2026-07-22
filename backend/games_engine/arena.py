"""Arena bot-coding: CodeClash-style — write a bot, watch it fight, iterate.

Three rounds, each two phases:

1. **edit** — both seats submit a bot (`submit_bot`, open action `payload:
   "code"`): a `bot(obs) -> move` function. Round 1 gives a long edit window; later
   rounds reuse the ruleset's `edit_phase_s` (the referee's move clock). The
   *previous* round's full tick log is in the edit-phase `public_state`, so
   studying the loss and iterating is the whole game.
2. **compete** — `WORK`: the server simulates the two bots head-to-head on a
   seeded grid (`games_engine/botsim.py`), each bot isolated in its own subprocess.

Round wins decide the match (`returns()` is the round differential). Chance nodes
seed each round differently so a bot can't overfit one board.
"""

from __future__ import annotations

import random
from typing import Any

from backend.games_engine import botsim
from backend.games_engine.base import (
    CHANCE,
    TERMINAL,
    WORK,
    Action,
    GameSpec,
    GameState,
    register_game,
)

ROUNDS = 3
MAX_CODE_CHARS = 20_000
EDIT_TIMEOUT_S = 300.0

STARTER_BOT = """\
def bot(obs):
    # obs: me=[x,y], opponent=[x,y], pellets=[[x,y],...], grid=N, my_score, opponent_score
    mx, my = obs["me"]
    best = None
    best_dist = 999
    for px, py in obs["pellets"]:
        dist = abs(px - mx) + abs(py - my)
        if dist < best_dist:
            best, best_dist = (px, py), dist
    if best is None:
        return "stay"
    tx, ty = best
    if tx > mx:
        return "right"
    if tx < mx:
        return "left"
    if ty > my:
        return "down"
    if ty < my:
        return "up"
    return "stay"
"""


class ArenaGame(GameState):
    def __init__(self) -> None:
        self.round = 0
        self.phase = "edit"  # edit -> (WORK compete) -> edit ... -> done
        self.bots: list[str | None] = [None, None]
        self.round_wins = [0, 0]
        self.round_seed: int | None = None
        # Per-round result from botsim: {scores, winner, ticks, forfeits}.
        self.round_logs: list[dict[str, Any]] = []

    # ---- turn structure ----------------------------------------------------

    def current_players(self) -> list[int]:
        if self.phase != "edit" or self.round >= ROUNDS:
            return []
        return [s for s in (0, 1) if self.bots[s] is None]

    def current_player(self) -> int:
        if self.round >= ROUNDS:
            return TERMINAL
        if self.phase == "edit":
            pending = self.current_players()
            if pending:
                return pending[0]
            # Both bots in → need a seed (chance) then the compete phase (work).
            if self.round_seed is None:
                return CHANCE
            return WORK
        return WORK

    def resolve_chance(self, rng: random.Random) -> None:
        self.round_seed = rng.randrange(1_000_000)

    def legal_actions(self, player: int) -> list[Action]:
        if player not in self.current_players():
            return []
        return [
            Action(
                id="submit_bot",
                label=f"submit your bot for round {self.round + 1}",
                params={"payload": "code", "max_code_chars": MAX_CODE_CHARS},
            )
        ]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player not in self.current_players():
            raise ValueError("not accepting a bot from this seat right now")
        if action_id != "submit_bot":
            raise ValueError(f"bad action id {action_id!r}")
        code = payload.get("code") if isinstance(payload, dict) else payload
        self.bots[player] = str(code or "")[:MAX_CODE_CHARS]

    def run_work(self) -> None:
        """Simulate the round (blocking; the referee runs it off-loop), score it,
        and set up the next edit phase — or finish."""
        assert self.round_seed is not None
        result = botsim.simulate(
            self.bots[0] or "", self.bots[1] or "", self.round_seed
        )
        if result.winner is not None:
            self.round_wins[result.winner] += 1
        self.round_logs.append(
            {
                "round": self.round + 1,
                "scores": result.scores,
                "winner": result.winner,
                "forfeits": result.forfeits,
                "ticks": result.ticks,
            }
        )
        self.round += 1
        if self.round < ROUNDS:
            # Fresh edit phase: keep the bots as the default (players may resubmit)
            # but reset the seed so the next compete needs a new chance draw.
            self.phase = "edit"
            self.bots = [None, None]
            self.round_seed = None
        else:
            self.phase = "done"

    # ---- views -------------------------------------------------------------

    def observation(self, player: int) -> dict[str, Any]:
        return {
            "game": "arena",
            "seat": player,
            "round": self.round + 1,
            "rounds": ROUNDS,
            "grid": botsim.GRID,
            "ticks_per_round": botsim.TICKS,
            "rules": (
                "Write bot(obs) returning up/down/left/right/stay. Collect pellets; "
                "step onto the opponent's just-vacated cell to steal points."
            ),
            "starter_bot": STARTER_BOT,
            "round_wins": list(self.round_wins),
            # Study the last round you played (the loss) to iterate.
            "last_round": self.round_logs[-1] if self.round_logs else None,
        }

    def public_state(self) -> dict[str, Any]:
        return {
            "game": "arena",
            "round": self.round + 1 if self.round < ROUNDS else ROUNDS,
            "rounds": ROUNDS,
            "phase": "compete" if self.current_player() == WORK else self.phase,
            "grid": botsim.GRID,
            "round_wins": list(self.round_wins),
            "round_logs": self.round_logs,  # full tick logs for canvas playback
            "submitted": [b is not None for b in self.bots],
            "turn": None,
            "winner": self._winner() if self.is_terminal() else None,
        }

    # ---- outcome -----------------------------------------------------------

    def _winner(self) -> int | None:
        if self.round_wins[0] == self.round_wins[1]:
            return None
        return 0 if self.round_wins[0] > self.round_wins[1] else 1

    def returns(self) -> dict[int, float]:
        diff = float(self.round_wins[0] - self.round_wins[1])
        return {0: diff, 1: -diff}


SPEC = register_game(
    GameSpec(
        id="arena",
        name="Arena",
        min_players=2,
        max_players=2,
        factory=ArenaGame,
        move_timeout_s=EDIT_TIMEOUT_S,
        decision_class="policy",
        default_policy="bot",
        pacing="realtime",
    )
)
