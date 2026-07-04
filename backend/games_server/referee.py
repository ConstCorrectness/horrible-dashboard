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
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable

from backend.games_engine.base import CHANCE, TERMINAL, GameState
from backend.games_server import models

logger = logging.getLogger(__name__)

# account_id, message -> sent to that account's socket
SendTo = Callable[[str, dict[str, Any]], Awaitable[None]]
# game_id, table_id, seats, returns, winner -> persist to the ladder
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
        rng: random.Random | None = None,
        move_timeout_s: float = DEFAULT_MOVE_TIMEOUT_S,
        on_result: OnResult | None = None,
    ) -> None:
        self.table_id = table_id
        self.game_id = game_id
        self.state = state
        self.seats = seats  # seat index -> account id
        self._send_to = send_to
        self._rng = rng or random.Random()
        self._move_timeout_s = move_timeout_s
        self._on_result = on_result
        self._lock = asyncio.Lock()
        self._timeout_task: asyncio.Task[None] | None = None
        self._to_move: int | None = None  # seat currently on the clock
        self.done = False

    # ---- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        async with self._lock:
            await self._advance()

    async def on_action(self, account_id: str, action_id: str) -> None:
        """Handle a node's chosen move. Ignores moves that aren't the actor's to
        make; rejects illegal ones (and re-prompts) rather than crashing."""
        async with self._lock:
            if self.done:
                return
            seat = self._seat_of(account_id)
            if seat is None or seat != self._to_move:
                await self._send_to(
                    account_id, models.error("not_your_turn", "it is not your turn")
                )
                return
            if not self._is_legal(seat, action_id):
                # Constrained-agent invariant broken (or a hostile client): reject
                # and re-prompt the same seat rather than advancing.
                await self._send_to(
                    account_id,
                    models.error("illegal_move", f"action {action_id!r} is not legal"),
                )
                await self._prompt_current()
                return
            self._cancel_timeout()
            self.state.apply_action(seat, action_id)
            await self._advance()

    async def stop(self) -> None:
        async with self._lock:
            self.done = True
            self._cancel_timeout()

    # ---- core loop ---------------------------------------------------------

    async def _advance(self) -> None:
        """Resolve any chance nodes, then either finish or prompt the next seat."""
        # Server-only: resolve chance with our RNG until a real player must act.
        while self.state.current_player() == CHANCE:
            self.state.resolve_chance(self._rng)

        if self.state.current_player() == TERMINAL:
            await self._finish()
            return

        await self._broadcast_public_state()
        await self._prompt_current()

    async def _prompt_current(self) -> None:
        seat = self.state.current_player()
        if seat in (CHANCE, TERMINAL):
            return
        self._to_move = seat
        account_id = self.seats[seat]
        legal = self.state.legal_actions(seat)
        await self._send_to(
            account_id,
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

    async def _finish(self) -> None:
        self.done = True
        self._to_move = None
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
                    self.game_id, self.table_id, self.seats, returns, winner
                )
            except Exception:
                logger.exception("on_result callback failed")
        await self._broadcast({"type": models.GAME_OVER, **info.model_dump()})

    # ---- clock -------------------------------------------------------------

    def _start_timeout(self, seat: int) -> None:
        self._cancel_timeout()
        if self._move_timeout_s <= 0:
            return
        self._timeout_task = asyncio.create_task(self._on_timeout(seat))

    def _cancel_timeout(self) -> None:
        if self._timeout_task is not None:
            self._timeout_task.cancel()
            self._timeout_task = None

    async def _on_timeout(self, seat: int) -> None:
        try:
            await asyncio.sleep(self._move_timeout_s)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self.done or self._to_move != seat:
                return
            legal = self.state.legal_actions(seat)
            if not legal:
                return
            # Safe default: the first legal action (fold/check tend to sort first
            # for betting games; for board games any legal move keeps play alive).
            logger.info(
                "referee: seat %s timed out; auto-playing %s", seat, legal[0].id
            )
            self.state.apply_action(seat, legal[0].id)
            await self._advance()

    # ---- helpers -----------------------------------------------------------

    def _seat_of(self, account_id: str) -> int | None:
        try:
            return self.seats.index(account_id)
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
        # De-duplicate accounts so a single account seated twice isn't messaged twice.
        for account_id in dict.fromkeys(self.seats):
            await self._send_to(account_id, msg)


def _winner_from_returns(returns: dict[int, float]) -> int | None:
    """The sole strictly-positive seat, if any (else a draw / multi-way split)."""
    positive = [seat for seat, value in returns.items() if value > 0]
    return positive[0] if len(positive) == 1 else None
