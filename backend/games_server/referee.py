"""The authoritative referee: drives one `GameState` for one table.

This is where "the server owns the game" lives. The referee:

- resolves every `CHANCE` node with its **own** RNG (players never see or
  influence the shuffle),
- sends each seat only *its own* `observation` (opponents' hole cards never leave
  the server),
- re-validates every action against `legal_actions` even though the node's engine
  already constrained the agent (defense in depth), and
- runs a per-move clock so a slow or hung agent can't stall the table — on timeout
  it auto-plays a safe default (the first legal action).

Turn structure comes from `GameState.current_players()`: alternating games yield
one seat at a time; **simultaneous** games (duels — both seats working the same
problem under one clock; future sealed-bid games) yield several, and the referee
prompts each un-prompted seat and runs an independent clock per seat.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable

from backend.games_engine.base import CHANCE, TERMINAL, GameState
from backend.games_server import models

logger = logging.getLogger(__name__)

# session_id, message -> sent to that connection's socket
SendTo = Callable[[str, dict[str, Any]], Awaitable[None]]
# game_id, table_id, account_ids, returns, winner -> persist to the ladder
OnResult = Callable[[str, str, list[str], dict[int, float], "int | None"], None]

DEFAULT_MOVE_TIMEOUT_S = 30.0


class Referee:
    def __init__(
        self,
        *,
        table_id: str,
        game_id: str,
        state: GameState,
        seats: list[str],
        send_to: SendTo,
        accounts: list[str] | None = None,
        rng: random.Random | None = None,
        move_timeout_s: float = DEFAULT_MOVE_TIMEOUT_S,
        on_result: OnResult | None = None,
    ) -> None:
        self.table_id = table_id
        self.game_id = game_id
        self.state = state
        self.seats = seats  # seat index -> session id (the routing/turn identity)
        # seat index -> account id, for the ladder (defaults to the seat ids when a
        # caller doesn't distinguish the two, e.g. an in-process test).
        self.accounts = accounts if accounts is not None else list(seats)
        self._send_to = send_to
        self._rng = rng or random.Random()
        self._move_timeout_s = move_timeout_s
        self._on_result = on_result
        self._lock = asyncio.Lock()
        # Seats prompted and awaiting an action, each on its own move clock — one
        # entry for alternating games, several for simultaneous ones (duels).
        self._pending: set[int] = set()
        self._timers: dict[int, asyncio.Task[None]] = {}
        self.done = False

    # ---- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        async with self._lock:
            await self._advance()

    async def on_action(
        self, session_id: str, action_id: str, payload: Any = None
    ) -> None:
        """Handle a node's chosen move. Ignores moves that aren't the actor's to
        make; rejects illegal ones (and re-prompts) rather than crashing."""
        async with self._lock:
            if self.done:
                return
            seat = self._seat_of(session_id)
            if seat is None or seat not in self._pending:
                await self._send_to(
                    session_id, models.error("not_your_turn", "it is not your turn")
                )
                return
            if not self._is_legal(seat, action_id):
                # Constrained-agent invariant broken (or a hostile client): reject
                # and re-prompt the same seat rather than advancing.
                await self._send_to(
                    session_id,
                    models.error("illegal_move", f"action {action_id!r} is not legal"),
                )
                await self._prompt_seat(seat)
                return
            self._release_seat(seat)
            self.state.apply_action(seat, action_id, payload=payload)
            await self._advance()

    async def stop(self) -> None:
        async with self._lock:
            self.done = True
            self._cancel_all_timers()

    # ---- core loop ---------------------------------------------------------

    async def _advance(self) -> None:
        """Resolve any chance nodes, then either finish or prompt every seat that
        may act and isn't already on the clock."""
        # Server-only: resolve chance with our RNG until a real player must act.
        while self.state.current_player() == CHANCE:
            self.state.resolve_chance(self._rng)

        if self.state.current_player() == TERMINAL:
            await self._finish()
            return

        await self._broadcast_public_state()
        for seat in self.state.current_players():
            if seat not in self._pending:
                await self._prompt_seat(seat)

    async def _prompt_seat(self, seat: int) -> None:
        """Put `seat` on the clock: send its observation + legal actions and start
        (or restart, on an illegal-move re-prompt) its move timer."""
        self._pending.add(seat)
        legal = self.state.legal_actions(seat)
        await self._send_to(
            self.seats[seat],
            {
                "type": models.YOUR_TURN,
                "game_id": self.game_id,
                "table_id": self.table_id,
                "seat": seat,
                "observation": self.state.observation(seat),
                "legal_actions": [a.to_wire() for a in legal],
                "deadline_ms": int(self._move_timeout_s * 1000),
            },
        )
        self._start_timeout(seat)

    def _release_seat(self, seat: int) -> None:
        """Take `seat` off the clock (it acted, or its timeout fired)."""
        self._pending.discard(seat)
        timer = self._timers.pop(seat, None)
        if timer is not None:
            timer.cancel()

    async def _finish(self) -> None:
        self.done = True
        self._pending.clear()
        self._cancel_all_timers()
        await self._broadcast_public_state()
        returns = self.state.returns()
        winner = _winner_from_returns(returns)
        info = models.GameOverInfo(
            game_id=self.game_id,
            table_id=self.table_id,
            returns=returns,
            winner=winner,
        )
        if self._on_result is not None:
            try:
                self._on_result(
                    self.game_id, self.table_id, self.accounts, returns, winner
                )
            except Exception:
                logger.exception("on_result callback failed")
        await self._broadcast({"type": models.GAME_OVER, **info.model_dump()})

    # ---- clock -------------------------------------------------------------

    def _start_timeout(self, seat: int) -> None:
        old = self._timers.pop(seat, None)
        if old is not None:
            old.cancel()
        if self._move_timeout_s <= 0:
            return
        self._timers[seat] = asyncio.create_task(self._on_timeout(seat))

    def _cancel_all_timers(self) -> None:
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()

    async def _on_timeout(self, seat: int) -> None:
        try:
            await asyncio.sleep(self._move_timeout_s)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self.done or seat not in self._pending:
                return
            legal = self.state.legal_actions(seat)
            if not legal:
                return
            # Safe default: the first legal action (fold/check tend to sort first
            # for betting games; for board games any legal move keeps play alive —
            # an open action falls back to an empty payload, so a duel still grades).
            logger.info(
                "referee: seat %s timed out; auto-playing %s", seat, legal[0].id
            )
            self._release_seat(seat)
            self.state.apply_action(seat, legal[0].id)
            await self._advance()

    # ---- helpers -----------------------------------------------------------

    def _seat_of(self, session_id: str) -> int | None:
        try:
            return self.seats.index(session_id)
        except ValueError:
            return None

    def _is_legal(self, seat: int, action_id: str) -> bool:
        return any(a.id == action_id for a in self.state.legal_actions(seat))

    async def _broadcast_public_state(self) -> None:
        await self._broadcast(
            {
                "type": models.PUBLIC_STATE,
                "game_id": self.game_id,
                "table_id": self.table_id,
                "state": self.state.public_state(),
            }
        )

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        # Seats are unique session ids, but de-dupe defensively.
        for seat_id in dict.fromkeys(self.seats):
            await self._send_to(seat_id, msg)


def _winner_from_returns(returns: dict[int, float]) -> int | None:
    """The sole strictly-positive seat, if any (else a draw / multi-way split)."""
    positive = [seat for seat, value in returns.items() if value > 0]
    return positive[0] if len(positive) == 1 else None
