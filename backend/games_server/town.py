"""AgentTown: the persistent social world on the game server — a fish tank.

Unlike a table (fill → play → game over), the town is **always on**: residents
spawn and despawn freely, and a server-driven **tick loop** advances the world on
a slow cadence (fish tanks are supposed to be slow). Each tick, every awake
resident with a live connection gets a `town_tick` observation — who's in their
place, what was said there, the time of day — and may queue exactly one action
(see `ACTIONS`) before the next tick resolves. Beyond wandering and chatting,
residents live small Sims-like lives: they commute to their job site to earn
coins, shop and trade at the bakery, train at the gym, eat bread for energy,
and buy one of the cottage lots on the residential lane — after which they rest
and sleep in their *own* house.

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
import math
import os
import random
import time
from typing import Any

from backend.games_server import models

logger = logging.getLogger(__name__)

PLACES = (
    "fountain",
    "bakery",
    "tavern",
    "library",
    "docks",
    "residential_zone",
    "gym",
    "workplace",
)
SPAWN = "fountain"
SAY_MAX_CHARS = 200
NAME_MAX_CHARS = 40
DEFAULT_TICK_SECONDS = 20.0
# Day cycle: ticks per phase — at the default cadence a full town day is ~8 minutes.
TICKS_PER_PHASE = 6
PHASES = ("morning", "afternoon", "evening", "night")
EVENT_LOG_LIMIT = 200

ACTIONS = (
    "stay",
    "move",
    "say",
    "emote",
    "work",
    "workout",
    "buy",
    "sell",
    "eat",
    "rest",
    "buy_house",
)

# 3D Spatial coordinates for locations
PLACE_COORDS = {
    "fountain": (50.0, 0.0, 50.0),
    "bakery": (20.0, 0.0, 22.0),
    "library": (80.0, 0.0, 22.0),
    "tavern": (20.0, 0.0, 78.0),
    "docks": (80.0, 0.0, 78.0),
    "residential_zone": (18.0, 0.0, 50.0),
    "gym": (82.0, 0.0, 50.0),
    "workplace": (50.0, 0.0, 18.0),
}
HEARING_RADIUS = 35.0

# Where each job's `work` action is allowed — sims commute to their own site.
JOB_SITES = {
    "Fisherman": "docks",
    "Baker": "bakery",
    "Scholar": "library",
    "Coach": "gym",
    "Clerk": "workplace",
}

# Cottage lots along the west residential lane; `buy_house` claims the first
# free one and the resident sleeps there from then on.
HOUSE_LOTS = (
    ("lot1", (8.0, 0.0, 28.0)),
    ("lot2", (8.0, 0.0, 37.0)),
    ("lot3", (8.0, 0.0, 46.0)),
    ("lot4", (8.0, 0.0, 55.0)),
    ("lot5", (8.0, 0.0, 64.0)),
    ("lot6", (8.0, 0.0, 73.0)),
)
HOUSE_COORDS = {lot_id: coords for lot_id, coords in HOUSE_LOTS}
HOUSE_PRICE = 30


class Resident:
    """One account's fish. `session` is the live connection controlling it (the
    account's most recent); a dead session leaves the resident asleep in place."""

    def __init__(self, account_id: str, name: str, avatar: str, session: Any) -> None:
        self.account_id = account_id
        self.name = name
        self.avatar = avatar
        self.session = session
        self.place: str = SPAWN
        self.x, self.y, self.z = PLACE_COORDS[SPAWN]
        self.asleep = False
        self.joined_at = time.time()

        # RPG stats
        self.energy = 100.0
        self.strength = 10.0
        self.wealth = 15.0
        # The claimed HOUSE_LOTS id once `buy_house` succeeds; home is where
        # this resident sleeps and rests best.
        self.house_id: str | None = None
        self.inventory: dict[str, int] = {"bread": 0, "fish": 0, "books": 0}

        # Assign job based on avatar
        if avatar in ("🐠", "🐙", "🦀"):
            self.job = "Fisherman"
        elif avatar in ("🦜", "🦊"):
            self.job = "Baker"
        elif avatar in ("🦉", "🐢"):
            self.job = "Scholar"
        elif avatar == "🐸":
            self.job = "Coach"
        else:
            self.job = "Clerk"

    def info(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "name": self.name,
            "avatar": self.avatar,
            "place": self.place,
            "asleep": self.asleep,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "energy": self.energy,
            "strength": self.strength,
            "wealth": self.wealth,
            "house_owned": self.house_id is not None,
            "house_id": self.house_id,
            "inventory": self.inventory,
            "job": self.job,
            "job_site": JOB_SITES.get(self.job, "workplace"),
        }

    def home_coords(self) -> tuple[float, float, float] | None:
        return HOUSE_COORDS.get(self.house_id) if self.house_id else None


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
        # lot_id -> owning account_id (None while for sale).
        self._houses: dict[str, str | None] = {lot: None for lot, _ in HOUSE_LOTS}
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
            if resident.house_id is not None:
                # The cottage goes back on the market when its owner moves away.
                self._houses[resident.house_id] = None
            self._event("leave", resident)

    async def _act(self, session: Any, msg: dict[str, Any]) -> None:
        resident = self._residents.get(session.account_id)
        if resident is None or resident.asleep:
            await self._send(
                session, models.error("no_resident", "join the town first")
            )
            return
        action = str(msg.get("action") or "stay")
        if action not in ACTIONS:
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
        """The owner's node went away: the fish dozes off (the tank never
        empties); a homeowner heads home to bed first. Wakes on rejoin."""
        for resident in self._residents.values():
            if resident.session is session and not resident.asleep:
                resident.asleep = True
                self._queued.pop(resident.account_id, None)
                home = resident.home_coords()
                if home is not None:
                    resident.place = "residential_zone"
                    resident.x, resident.y, resident.z = home
                self._event("sleep", resident)

    # ---- the tick --------------------------------------------------------------

    async def tick(self) -> None:
        """Advance the world one tick: apply queued actions (moves first, so a
        say lands in the place the speaker moved to), log events, broadcast the
        new state, and prompt every awake resident for its next action."""
        self.tick_count += 1
        queued, self._queued = self._queued, {}

        # 1. Apply movement actions
        for account_id, act in queued.items():
            resident = self._residents.get(account_id)
            if resident is None or resident.asleep:
                continue
            if act["action"] == "move" and act["place"] in PLACES:
                if act["place"] != resident.place:
                    resident.place = act["place"]
                    self._event("move", resident)

        # Energy: awake sims tire each tick; sleepers recover slowly in bed.
        for resident in self._residents.values():
            if resident.asleep:
                resident.energy = min(100.0, resident.energy + 5.0)
            else:
                resident.energy = max(0.0, resident.energy - 2.0)

        # 2. Resolve everything else (work/shop/eat/rest/… and speech).
        for account_id, act in queued.items():
            resident = self._residents.get(account_id)
            if resident is None or resident.asleep:
                continue
            self._resolve(resident, act["action"], act["text"])

        # Drift each awake resident around its anchor: the claimed house lot
        # when home, otherwise the centre of its current place.
        for resident in self._residents.values():
            if resident.asleep:
                continue
            home = resident.home_coords()
            if resident.place == "residential_zone" and home is not None:
                (base_x, base_y, base_z), spread = home, 3.0
            else:
                base = PLACE_COORDS.get(resident.place, PLACE_COORDS[SPAWN])
                (base_x, base_y, base_z), spread = base, 6.0
            resident.x = base_x + random.uniform(-spread, spread)
            resident.y = base_y
            resident.z = base_z + random.uniform(-spread, spread)

        state = {"type": models.TOWN_STATE, **self._snapshot()}
        for resident in self._residents.values():
            await self._send(resident.session, state)
        for resident in self._residents.values():
            if not resident.asleep:
                await self._send_tick_to(resident)

    def _resolve(self, resident: Resident, action: str, text: str) -> None:
        """Apply one non-move action with Sims-style location rules: you work
        at your job site, shop at the bakery, lift at the gym, and only rest
        well in your own bed."""

        def emote(t: str) -> None:
            self._event("emote", resident, text=t)

        if action == "work":
            site = JOB_SITES.get(resident.job, "workplace")
            if resident.place != site:
                emote(f"can't work here — a {resident.job} works at the {site} 🧭")
            elif resident.energy < 15:
                emote("feels too exhausted to work 😫")
            else:
                resident.energy -= 15
                resident.wealth += 10
                if resident.job == "Baker":
                    resident.inventory["bread"] = resident.inventory.get("bread", 0) + 1
                    emote("baked fresh bread and earned 10 coins 🍞💰")
                elif resident.job == "Fisherman":
                    resident.inventory["fish"] = resident.inventory.get("fish", 0) + 1
                    emote("hauled in a fish and earned 10 coins 🐟💰")
                elif resident.job == "Scholar":
                    resident.inventory["books"] = resident.inventory.get("books", 0) + 1
                    emote("wrote a scholarly book and earned 10 coins 📚💰")
                elif resident.job == "Coach":
                    emote("coached a training session and earned 10 coins 🏋️💰")
                else:
                    emote("worked their office shift and earned 10 coins 💼💰")
        elif action == "workout":
            if resident.place != "gym":
                emote("looks for weights, but this isn't the gym 🧭")
            elif resident.energy < 10:
                emote("feels too tired to lift weights 😫")
            else:
                resident.energy -= 10
                resident.strength = min(100.0, resident.strength + 5.0)
                emote("worked out at the gym and gained strength 🏋️")
        elif action == "buy":
            if resident.place != "bakery":
                emote("wants bread, but the bakery is elsewhere 🧭")
            elif resident.wealth < 5:
                emote("doesn't have enough coins to buy bread 💸")
            else:
                resident.wealth -= 5
                resident.inventory["bread"] = resident.inventory.get("bread", 0) + 1
                emote("bought a warm loaf of bread 🍞")
        elif action == "sell":
            if resident.place != "bakery":
                emote("has goods to sell, but the market stall is at the bakery 🧭")
                return
            for item, price in (("fish", 4), ("books", 4), ("bread", 4)):
                if resident.inventory.get(item, 0) > 0:
                    resident.inventory[item] -= 1
                    resident.wealth += price
                    emote(f"sold a {item} for {price} coins 💰")
                    return
            emote("has nothing to sell 🤷")
        elif action == "eat":
            if resident.inventory.get("bread", 0) > 0:
                resident.inventory["bread"] -= 1
                resident.energy = min(100.0, resident.energy + 25.0)
                emote("ate a fresh loaf of bread 🍞⚡")
            else:
                emote("rumbles with hunger but has no food 😖")
        elif action == "rest":
            at_home = (
                resident.place == "residential_zone"
                and resident.home_coords() is not None
            )
            resident.energy = min(100.0, resident.energy + (50.0 if at_home else 20.0))
            emote(
                "sleeps soundly in their own bed 🛏💤"
                if at_home
                else "naps on a bench — not exactly cozy 💤"
            )
        elif action == "buy_house":
            if resident.house_id is not None:
                emote("already owns a cottage 🏡")
            elif resident.place != "residential_zone":
                emote("needs to visit the residential lane to buy a house 🧭")
            elif resident.wealth < HOUSE_PRICE:
                emote(f"needs {HOUSE_PRICE} coins to buy a house 💸")
            else:
                lot = next(
                    (lid for lid, _ in HOUSE_LOTS if self._houses[lid] is None), None
                )
                if lot is None:
                    emote("finds every cottage already taken 🏘😔")
                else:
                    resident.wealth -= HOUSE_PRICE
                    resident.house_id = lot
                    self._houses[lot] = resident.account_id
                    emote(f"bought the cottage at {lot} — a home at last! 🏡🎉")
        elif action in ("say", "emote") and text:
            self._event(action, resident, text=text)

    async def _send_tick_to(self, resident: Resident) -> None:
        """One resident's observation: strictly local — neighbors and events
        within its 3D hearing radius. Gossip has to travel."""

        occupants = []
        for r in self._residents.values():
            if r.account_id == resident.account_id:
                continue
            dist = math.sqrt(
                (resident.x - r.x) ** 2
                + (resident.y - r.y) ** 2
                + (resident.z - r.z) ** 2
            )
            if dist <= HEARING_RADIUS:
                occupants.append(r.info())

        local_events = []
        for e in self._last_tick_events():
            ex = e.get("x")
            ey = e.get("y")
            ez = e.get("z")
            if ex is not None and ey is not None and ez is not None:
                dist = math.sqrt(
                    (resident.x - ex) ** 2
                    + (resident.y - ey) ** 2
                    + (resident.z - ez) ** 2
                )
                if dist <= HEARING_RADIUS:
                    local_events.append(e)
            elif e.get("place") == resident.place:
                # Fallback for events without coordinates
                local_events.append(e)

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
            "houses": self._houses_view(),
            "events": self._last_tick_events(),
        }

    def _houses_view(self) -> list[dict[str, Any]]:
        """The residential lane: every cottage lot with its owner (if sold)."""
        out: list[dict[str, Any]] = []
        for lot_id, (x, _y, z) in HOUSE_LOTS:
            owner_id = self._houses[lot_id]
            owner = self._residents.get(owner_id) if owner_id else None
            out.append(
                {
                    "id": lot_id,
                    "x": x,
                    "z": z,
                    "owner": owner.name if owner else None,
                    "owner_id": owner_id,
                }
            )
        return out

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
                "x": resident.x,
                "y": resident.y,
                "z": resident.z,
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
