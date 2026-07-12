"""The ranked matchmaking queue.

One bucket per `(game_id, difficulty)`. Pairing is rating-window based: a fresh
entry looks ±75 around its rating, widening by +50 every 10 seconds waiting, so
close matches happen fast and lopsided ones only when the pool is thin. When no
human appears within the backfill deadline (`GAMES_QUEUE_BOT_S`, default 45s) — or
immediately for a **placement** run — the queue seats a practice bot at the
nearest tier instead, so ranked always works solo.

Difficulty gating: `standard` is open to everyone; harder difficulties unlock by
tier (`store.DIFFICULTY_GATES`) — the skill curve made structural.

The hub owns one `Matchmaker` and runs `sweep()` on a short interval task (the
AgentTown loop pattern); pairing calls back into the hub to host + seat tables.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.games_server import store

if TYPE_CHECKING:
    from backend.games_server.hub import GameHub, Session

logger = logging.getLogger(__name__)

BASE_WINDOW = 75.0
WINDOW_STEP = 50.0  # widen by this every WINDOW_STEP_S waiting
WINDOW_STEP_S = 10.0
STATUS_EVERY_S = 5.0

SWEEP_INTERVAL_S = 2.0


def _bot_backfill_s() -> float:
    return float(os.environ.get("GAMES_QUEUE_BOT_S", "45"))


@dataclass
class _Entry:
    session: "Session"
    account_id: str
    game_id: str
    difficulty: str
    rating: float
    placement: bool
    joined_at: float = field(default_factory=time.time)
    last_status: float = 0.0

    def window(self, now: float) -> float:
        waited = now - self.joined_at
        return BASE_WINDOW + WINDOW_STEP * int(waited // WINDOW_STEP_S)


class Matchmaker:
    def __init__(self, hub: "GameHub") -> None:
        self._hub = hub
        # session_id -> entry (one queue slot per connection).
        self._entries: dict[str, _Entry] = {}
        self._task: asyncio.Task[None] | None = None

    # ---- lifecycle -----------------------------------------------------------

    def start_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop_loop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(SWEEP_INTERVAL_S)
                try:
                    await self.sweep()
                except Exception:
                    logger.exception("matchmaking sweep failed")
        except asyncio.CancelledError:
            pass

    # ---- queue membership ------------------------------------------------------

    async def join(
        self,
        session: "Session",
        game_id: str,
        difficulty: str = "standard",
        placement: bool = False,
    ) -> dict[str, Any] | None:
        """Enter the queue. Returns an error dict (for the caller to send) when the
        difficulty is tier-locked; None on success."""
        assert session.account_id is not None
        rating_row = store.get_rating(session.account_id, game_id)
        rating = rating_row["rating"] if rating_row else store.BASE_RATING
        placement_games = rating_row["placement_games"] if rating_row else 0
        tier = store.tier_for(rating, placement_games)
        gate = store.DIFFICULTY_GATES.get(difficulty, "bronze")
        if difficulty != "standard" and not store.tier_at_least(tier, gate):
            return {
                "code": "tier_locked",
                "message": f"{difficulty} unlocks at {gate} (you are {tier})",
            }
        self._entries[session.session_id] = _Entry(
            session=session,
            account_id=session.account_id,
            game_id=game_id,
            difficulty=difficulty,
            rating=rating,
            placement=placement or tier == "placement",
        )
        await self.sweep()
        return None

    def leave(self, session: "Session") -> None:
        self._entries.pop(session.session_id, None)

    def on_disconnect(self, session: "Session") -> None:
        self.leave(session)

    # ---- pairing ---------------------------------------------------------------

    async def sweep(self) -> None:
        """Pair compatible entries, backfill stale ones with bots, push statuses."""
        now = time.time()
        # Buckets keep pairing O(bucket²) on tiny pools — fine at this scale.
        buckets: dict[tuple[str, str], list[_Entry]] = {}
        for entry in self._entries.values():
            buckets.setdefault((entry.game_id, entry.difficulty), []).append(entry)

        for (game_id, difficulty), entries in buckets.items():
            entries.sort(key=lambda e: e.joined_at)
            paired: set[str] = set()
            for i, a in enumerate(entries):
                if a.session.session_id in paired:
                    continue
                for b in entries[i + 1 :]:
                    if b.session.session_id in paired:
                        continue
                    if a.account_id == b.account_id:
                        continue  # don't pair someone against their other device
                    gap = abs(a.rating - b.rating)
                    if gap <= min(a.window(now), b.window(now)):
                        paired.update((a.session.session_id, b.session.session_id))
                        await self._start_match(game_id, difficulty, a, b)
                        break
                else:
                    # No human partner: placement runs get a bot instantly, everyone
                    # else after the backfill deadline.
                    waited = now - a.joined_at
                    if a.placement or waited >= _bot_backfill_s():
                        paired.add(a.session.session_id)
                        await self._start_bot_match(game_id, difficulty, a)
            for sid in paired:
                self._entries.pop(sid, None)

        # Periodic queue_status so the Find Match button can show a live timer.
        for entry in self._entries.values():
            if now - entry.last_status >= STATUS_EVERY_S:
                entry.last_status = now
                try:
                    await entry.session.conn.send_json(
                        {
                            "type": "queue_status",
                            "game_id": entry.game_id,
                            "difficulty": entry.difficulty,
                            "waiting_s": int(now - entry.joined_at),
                            "window": entry.window(now),
                        }
                    )
                except Exception:
                    logger.debug("queue_status send failed", exc_info=True)

    async def _start_match(
        self, game_id: str, difficulty: str, a: _Entry, b: _Entry
    ) -> None:
        await self._hub.start_queue_match(game_id, difficulty, a.session, b.session)

    async def _start_bot_match(
        self, game_id: str, difficulty: str, entry: _Entry
    ) -> None:
        tier = _nearest_bot_tier(entry.rating)
        await self._hub.start_queue_match(
            game_id, difficulty, entry.session, None, bot_tier=tier
        )


def _nearest_bot_tier(rating: float) -> str:
    from backend.games_server.bots import TIER_RATINGS

    return min(TIER_RATINGS, key=lambda t: abs(TIER_RATINGS[t] - rating))
