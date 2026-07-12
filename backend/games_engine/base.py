"""The game-engine contract every game implements, plus a small registry.

This is intentionally OpenSpiel-shaped (see the package docstring): the same
interface has to describe tic-tac-toe *and* No-Limit Hold'em, so it separates
"whose turn is it" (which may be `CHANCE`) from "what can they do" and, crucially,
splits **per-player observation** (hides opponents' private state) from
**public state** (the spectator view, hides everyone's private state).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

# Sentinels for `current_player()`. Real seats are non-negative ints (0, 1, …).
CHANCE = -1  # a random event only the server resolves (deal/shuffle)
TERMINAL = -2  # the game is over; see returns()
WORK = -3  # server-side work (grading submissions); see run_work()


@dataclass(frozen=True)
class Action:
    """One legal move the agent may choose.

    `id` is the stable token the agent picks by (and the server re-validates
    against) — the agent never has to spell a move out in game notation. `label`
    is human/agent-readable. `params` carries structured extras (e.g. a raise
    amount) so a betting action can be both enumerable and precise.
    """

    id: str
    label: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "params": dict(self.params)}


@dataclass(frozen=True)
class GameSpec:
    """Static metadata about a game type (not a live game)."""

    id: str
    name: str
    min_players: int
    max_players: int
    factory: Callable[..., "GameState"]
    # Per-move clock override (seconds). Board games are fine with the referee's
    # default; agentic-task duels (e.g. a RAG race turn = answer a whole question
    # set) need minutes. None = use the referee's default.
    move_timeout_s: float | None = None

    def new(self, **kwargs: Any) -> "GameState":
        return self.factory(**kwargs)


class GameState:
    """A live game. Implementations mutate in place via `apply_action`.

    Perfect-information games can ignore `resolve_chance` (no chance nodes) and let
    `observation` fall back to `public_state`. Imperfect-information games override
    all three.
    """

    spec: GameSpec

    # ---- turn structure ----------------------------------------------------

    def current_player(self) -> int:
        """A seat index (0-based), or `CHANCE` / `TERMINAL`."""
        raise NotImplementedError

    def current_players(self) -> list[int]:
        """Every seat that may act *right now*. Alternating games get the default
        (the single `current_player`); **simultaneous** games (sealed bids, duels
        where both seats work the same problem under one clock) override this and
        the referee prompts every listed seat at once."""
        seat = self.current_player()
        return [] if seat in (CHANCE, TERMINAL) else [seat]

    def legal_actions(self, player: int) -> list[Action]:
        """The moves `player` may legally make right now (empty if not their turn)."""
        raise NotImplementedError

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        """Apply `player`'s chosen action. Raise `ValueError` if illegal — the
        server catches that and treats it as a rejected move.

        `payload` carries free-form data for **open actions** (an action whose
        `params` declare a `payload` kind, e.g. a RAG race's submitted answers or a
        bugfix duel's patch): the *choice* is still an enumerated legal action the
        referee validates, but its content is game-validated data, not an id.
        Enumerated-only games ignore it."""
        raise NotImplementedError

    def resolve_chance(self, rng: random.Random) -> None:
        """Resolve the pending chance event (shuffle/deal) using the server's RNG.
        Only called when `current_player() == CHANCE`. No-op by default."""
        raise NotImplementedError

    def run_work(self) -> None:
        """Perform pending **server-side work** (grading submissions, simulating a
        round). Only called when `current_player() == WORK`; the referee runs it
        off the event loop (`asyncio.to_thread`), so blocking here is fine — the
        table's lock is held, nothing else may act. Must make progress: after
        returning, `current_player()` must no longer be WORK (or eventually stop
        being WORK) or the referee would spin."""
        raise NotImplementedError

    # ---- views -------------------------------------------------------------

    def observation(self, player: int) -> dict[str, Any]:
        """What `player` is allowed to see. Defaults to the public state for
        perfect-information games; imperfect-info games add that seat's private
        state (e.g. hole cards)."""
        return self.public_state()

    def public_state(self) -> dict[str, Any]:
        """The spectator view — hides every player's private state."""
        raise NotImplementedError

    # ---- outcome -----------------------------------------------------------

    def is_terminal(self) -> bool:
        return self.current_player() == TERMINAL

    def returns(self) -> dict[int, float]:
        """Per-seat payoff once terminal (e.g. +1 win / -1 loss / 0 draw, or chip
        deltas for poker). Undefined before terminal."""
        raise NotImplementedError


# ---- registry --------------------------------------------------------------

_GAMES: dict[str, GameSpec] = {}


def register_game(spec: GameSpec) -> GameSpec:
    """Register a game type. Games self-register on import (see package init)."""
    _GAMES[spec.id] = spec
    return spec


def get_game(game_id: str) -> GameSpec:
    try:
        return _GAMES[game_id]
    except KeyError as exc:
        raise KeyError(f"unknown game '{game_id}'") from exc


def list_games() -> list[GameSpec]:
    return list(_GAMES.values())
