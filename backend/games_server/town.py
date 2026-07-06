"""AgentTown: the persistent social world on the game server — a fish tank.

Unlike a table (fill → play → game over), the town is **always on**: residents
spawn and despawn freely, and a server-driven **tick loop** advances the world on
a slow cadence (fish tanks are supposed to be slow). Each tick, every awake
resident with a live connection gets a `town_tick` observation — who's in their
place, what was said there, the time of day — and may queue exactly one action
(`stay` / `move` / `say` / `emote`) before the next tick resolves.

The server owns the *world* but never any resident's *mind*: personality, memory,
and decision-making live on the owner's node (see
`backend/modules/games/town_policy.py`). Identity follows the games account: one
resident per account, controlled by that account's most recent connection. When
the owner's node disconnects, the resident **falls asleep** in place (💤 on the
map) rather than vanishing — the tank never empties — and wakes on rejoin.

Locality is the observation discipline: a resident only sees events in its own
place, so gossip has to *travel* — an agent that wanders hears more.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from backend.games_server import models

logger = logging.getLogger(__name__)

PLACES = ("fountain", "bakery", "tavern", "library", "docks")
SPAWN = "fountain"
SAY_MAX_CHARS = 200
NAME_MAX_CHARS = 40
DEFAULT_TICK_SECONDS = 20.0
# Day cycle: ticks per phase — at the default cadence a full town day is ~8 minutes.
TICKS_PER_PHASE = 6
PHASES = ("morning", "afternoon", "evening", "night")
EVENT_LOG_LIMIT = 200


class Resident:
    """One account's fish. `session` is the live connection controlling it (the
    account's most recent); a dead session leaves the resident asleep in place."""

    def __init__(self, account_id: str, name: str, avatar: str, session: Any) -> None:
        self.account_id = account_id
        self.name = name
        self.avatar = avatar
        self.session = session
        self.place: str = SPAWN
        self.asleep = False
        self.joined_at = time.time()

    def info(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "name": self.name,
            "avatar": self.avatar,
            "place": self.place,
            "asleep": self.asleep,
        }


class TownHub:
    """The world: residents, the action queue, the event log, and the tick loop."""

    def __init__(self, tick_seconds: float | None = None) -> None:
        # Cadence: explicit arg (tests) > TOWN_TICK_SECONDS env > the slow default.
        if tick_seconds is None:
            tick_seconds = float(
                os.environ.get("TOWN_TICK_SECONDS", DEFAULT_TICK_SECONDS)
            )
        self._tick_seconds = tick_seconds
        self.tick_count = 0
        # account_id -> resident (one fish per account).
        self._residents: dict[str, Resident] = {}
        # account_id -> the action queued for the next tick (latest wins).
        self._queued: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._loop_task: asyncio.Task[None] | None = None

    # ---- lifecycle -----------------------------------------------------------

    def start_loop(self) -> None:
        """Start the world clock (called from the app lifespan; tests tick manually)."""
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._run_loop())

    def stop_loop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._tick_seconds)
            try:
                await self.tick()
            except Exception:
                logger.exception("town tick failed")

    # ---- dispatch (called by GameHub for town_* messages) ---------------------

    async def handle(self, session: Any, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == models.TOWN_JOIN:
            await self._join(session, msg)
        elif mtype == models.TOWN_LEAVE:
            await self._leave(session)
        elif mtype == models.TOWN_ACT:
            await self._act(session, msg)

    async def _join(self, session: Any, msg: dict[str, Any]) -> None:
        account_id = session.account_id
        name = str(msg.get("name") or session.display_name or account_id)[
            :NAME_MAX_CHARS
        ]
        avatar = str(msg.get("avatar") or "🐠")[:8]
        resident = self._residents.get(account_id)
        if resident is None:
            resident = Resident(account_id, name, avatar, session)
            self._residents[account_id] = resident
            self._event("arrive", resident)
        else:
            # Rejoin: the newest connection takes control; a sleeper wakes.
            resident.session = session
            resident.name, resident.avatar = name, avatar
            if resident.asleep:
                resident.asleep = False
                self._event("wake", resident)
        await self._send(
            session,
            {
                "type": models.TOWN_JOINED,
                "resident": resident.info(),
                **self._snapshot(),
            },
        )
        # Prompt the fresh fish immediately rather than making it wait a tick.
        await self._send_tick_to(resident)

    async def _leave(self, session: Any) -> None:
        resident = self._residents.get(session.account_id)
        if resident is not None and resident.session is session:
            del self._residents[session.account_id]
            self._queued.pop(session.account_id, None)
            self._event("leave", resident)

    async def _act(self, session: Any, msg: dict[str, Any]) -> None:
        resident = self._residents.get(session.account_id)
        if resident is None or resident.asleep:
            await self._send(
                session, models.error("no_resident", "join the town first")
            )
            return
        action = str(msg.get("action") or "stay")
        if action not in ("stay", "move", "say", "emote"):
            await self._send(
                session, models.error("bad_action", f"unknown town action {action!r}")
            )
            return
        # Latest queued action wins; it resolves at the next tick.
        self._queued[session.account_id] = {
            "action": action,
            "place": str(msg.get("place") or ""),
            "text": str(msg.get("text") or "")[:SAY_MAX_CHARS],
        }

    def on_disconnect(self, session: Any) -> None:
        """The owner's node went away: the fish dozes off in place (the tank never
        empties); it wakes when the account rejoins."""
        for resident in self._residents.values():
            if resident.session is session and not resident.asleep:
                resident.asleep = True
                self._queued.pop(resident.account_id, None)
                self._event("sleep", resident)

    # ---- the tick --------------------------------------------------------------

    async def tick(self) -> None:
        """Advance the world one tick: apply queued actions (moves first, so a
        say lands in the place the speaker moved to), log events, broadcast the
        new state, and prompt every awake resident for its next action."""
        self.tick_count += 1
        queued, self._queued = self._queued, {}
        for account_id, act in queued.items():
            resident = self._residents.get(account_id)
            if resident is None or resident.asleep:
                continue
            if act["action"] == "move" and act["place"] in PLACES:
                if act["place"] != resident.place:
                    resident.place = act["place"]
                    self._event("move", resident)
        for account_id, act in queued.items():
            resident = self._residents.get(account_id)
            if resident is None or resident.asleep:
                continue
            if act["action"] in ("say", "emote") and act["text"]:
                self._event(act["action"], resident, text=act["text"])

        state = {"type": models.TOWN_STATE, **self._snapshot()}
        for resident in self._residents.values():
            await self._send(resident.session, state)
        for resident in self._residents.values():
            if not resident.asleep:
                await self._send_tick_to(resident)

    async def _send_tick_to(self, resident: Resident) -> None:
        """One resident's observation: strictly local — its place, its neighbors,
        and this tick's events *in that place*. Gossip has to travel."""
        occupants = [
            r.info()
            for r in self._residents.values()
            if r.place == resident.place and r.account_id != resident.account_id
        ]
        local_events = [
            e for e in self._last_tick_events() if e.get("place") == resident.place
        ]
        await self._send(
            resident.session,
            {
                "type": models.TOWN_TICK,
                "tick": self.tick_count,
                "phase": self.phase(),
                "places": list(PLACES),
                "you": resident.info(),
                "occupants": occupants,
                "events": local_events,
            },
        )

    # ---- views -------------------------------------------------------------------

    def phase(self) -> str:
        return PHASES[(self.tick_count // TICKS_PER_PHASE) % len(PHASES)]

    def _snapshot(self) -> dict[str, Any]:
        return {
            "tick": self.tick_count,
            "phase": self.phase(),
            "places": list(PLACES),
            "residents": [r.info() for r in self._residents.values()],
            "events": self._last_tick_events(),
        }

    def _last_tick_events(self) -> list[dict[str, Any]]:
        return [e for e in self._events if e["tick"] == self.tick_count]

    def _event(self, etype: str, resident: Resident, text: str = "") -> None:
        self._events.append(
            {
                "tick": self.tick_count,
                "type": etype,
                "name": resident.name,
                "avatar": resident.avatar,
                "place": resident.place,
                "text": text,
            }
        )
        if len(self._events) > EVENT_LOG_LIMIT:
            del self._events[: len(self._events) - EVENT_LOG_LIMIT]

    async def _send(self, session: Any, msg: dict[str, Any]) -> None:
        try:
            await session.conn.send_json(msg)
        except Exception:
            logger.debug("town: failed to send to a session", exc_info=True)
