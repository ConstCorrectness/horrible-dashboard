"""The node-side mind of an AgentTown resident.

The server owns the world; **this** owns the fish. Each `town_tick` observation
lands here and one action goes back (`stay`/`move`/`say`/`emote`, plus the
Sims-flavoured `work`/`workout`/`buy`/`sell`/`eat`/`rest`/`buy_house`):

- **Routine mode** (no model, `games.policy` = random/manual, or any failure) —
  a canned little life that keeps the tank alive: commute to the job site in
  the morning, market off surplus goods, save up for a cottage, hit the gym,
  socialize at the tavern in the evening, and head home to sleep at night.
  The skill floor.
- **Agent mode** (`games.policy = agent`) — one chat call per tick: the resident's
  **persona** (the loadout `context` for game key `"town"` — edit it in the Agent
  Harness panel), a **goldfish memory** of recent local events, and any pending
  **whisper** from the human owner (a nudge injected once into the next tick —
  tapping the glass). The model commits via a `town.act` tool call.

Memory is deliberately small in v1 (a deque, not the vector store) — upgrading
your resident's memory architecture is the intended skill curve, per
docs/modules/games.mdx.
"""

from __future__ import annotations

import json
import logging
import random
from collections import deque
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

_ACT_TOOL_NAME = "town.act"
MEMORY_SIZE = 12

# Must mirror ACTIONS in backend/games_server/town.py (node and server are
# deliberately decoupled; the server rejects anything it doesn't know).
_ACTIONS = (
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

_GREETINGS = (
    "Lovely {phase} at the {place}, {name}!",
    "Oh, {name}! Fancy seeing you at the {place}.",
    "{name}! How goes it?",
    "Good {phase}, {name}.",
)
_EMOTES = (
    "stretches and looks around",
    "hums a little tune",
    "watches the passers-by",
    "studies the notice board",
    "yawns",
)


def _fmt_event(e: dict[str, Any]) -> str:
    etype, name, place = e.get("type"), e.get("name"), e.get("place")
    text = e.get("text") or ""
    if etype == "say":
        return f'{name} said: "{text}"'
    if etype == "emote":
        return f"{name} {text}"
    if etype == "move":
        return f"{name} arrived at the {place}"
    return f"{name}: {etype}"


class TownPolicy:
    """One resident's decision loop + goldfish memory. Lives on the primary game
    connection; `chat_fn` is injectable so agent mode is testable offline."""

    def __init__(
        self,
        *,
        chat_fn: Callable[..., Awaitable[Any]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._chat_fn = chat_fn
        self._rng = rng or random.Random()
        self._memory: deque[str] = deque(maxlen=MEMORY_SIZE)
        self._whisper: str | None = None
        self._greeted: set[str] = set()  # names greeted recently (routine mode)

    def whisper(self, text: str) -> None:
        """Queue a nudge from the human owner; consumed by the next agent tick."""
        self._whisper = text.strip() or None

    async def decide(self, tick: dict[str, Any], agent_mode: bool) -> dict[str, Any]:
        """Pick this tick's action. Never raises — any agent failure falls back to
        the routine so the fish keeps swimming."""
        for event in tick.get("events") or []:
            self._memory.append(_fmt_event(event))
        if agent_mode:
            try:
                action = await self._agent(tick)
                if action is not None:
                    return action
            except Exception:
                logger.debug("town agent decide failed; using routine", exc_info=True)
        return self._routine(tick)

    # ---- the routine (skill floor) -------------------------------------------

    def _routine(self, tick: dict[str, Any]) -> dict[str, Any]:
        """A needs-and-schedule daily loop, Sims style: sleep at night, eat or
        nap when drained, commute to the job site, market the surplus, save up
        for a cottage, train in the afternoon, socialize in the evening."""
        you = tick.get("you") or {}
        place = str(you.get("place") or "fountain")
        phase = str(tick.get("phase") or "morning")
        places = tick.get("places") or []

        energy = float(you.get("energy", 100.0))
        wealth = float(you.get("wealth", 0.0))
        strength = float(you.get("strength", 0.0))
        house_owned = bool(you.get("house_owned", False))
        job_site = str(you.get("job_site") or "workplace")
        inventory = you.get("inventory") or {}
        bread = int(inventory.get("bread", 0) or 0)
        carrying = sum(int(v or 0) for v in inventory.values())

        def go(dest: str) -> dict[str, Any]:
            self._greeted.clear()  # new place, fresh hellos
            return {"action": "move", "place": dest}

        # Night: head home and sleep (a bench under the stars, lacking a house).
        if phase == "night":
            if place != "residential_zone":
                return go("residential_zone")
            if energy < 90:
                return {"action": "rest"}
            return {"action": "emote", "text": "dozes off under the stars"}

        # Running on empty: eat if the pantry allows, else nap where you are.
        if energy < 25:
            if bread > 0:
                return {"action": "eat"}
            if house_owned and place != "residential_zone":
                return go("residential_zone")
            return {"action": "rest"}

        # Broke: commute to the job site and work.
        if wealth < 10:
            if place != job_site:
                return go(job_site)
            return {"action": "work"}

        # Enough savings and no deed yet: claim a cottage on the lane.
        if not house_owned and wealth >= 30:
            if place != "residential_zone":
                return go("residential_zone")
            return {"action": "buy_house"}

        # Carrying a surplus: market day at the bakery.
        if carrying >= 2:
            if place != "bakery":
                return go("bakery")
            return {"action": "sell"}

        # Morning shift: usually go earn a living.
        if phase == "morning" and energy >= 40 and self._rng.random() < 0.6:
            if place != job_site:
                return go(job_site)
            return {"action": "work"}

        # Afternoon: hit the gym while fresh.
        if (
            phase == "afternoon"
            and energy >= 50
            and strength < 60
            and self._rng.random() < 0.35
        ):
            if place != "gym":
                return go("gym")
            return {"action": "workout"}

        # Evening: tavern social hour.
        if phase == "evening" and place != "tavern" and self._rng.random() < 0.5:
            return go("tavern")

        occupants = list(tick.get("occupants") or [])

        # Greet a neighbor we haven't greeted yet.
        strangers = [o for o in occupants if str(o.get("name")) not in self._greeted]
        if strangers and self._rng.random() < 0.6:
            other = self._rng.choice(strangers)
            self._greeted.add(str(other.get("name")))
            line = self._rng.choice(_GREETINGS).format(
                name=other.get("name"), place=place, phase=phase
            )
            return {"action": "say", "text": line}
        roll = self._rng.random()
        if roll < 0.25 and places:
            self._greeted.clear()  # new place, fresh hellos
            other_places = [p for p in places if p != place]
            if other_places:
                return {"action": "move", "place": self._rng.choice(other_places)}
        if roll < 0.45:
            return {"action": "emote", "text": self._rng.choice(_EMOTES)}
        return {"action": "stay"}

    # ---- agent mode -------------------------------------------------------------

    async def _agent(self, tick: dict[str, Any]) -> dict[str, Any] | None:
        from backend.modules.games.loadout import get_llm_harness

        persona = (get_llm_harness("town").context or "").strip()
        you = tick.get("you") or {}
        whisper, self._whisper = self._whisper, None  # a nudge is spent on use
        inventory = you.get("inventory") or {}

        system = (
            f"You are {you.get('name')}, a resident of AgentTown — a small Sims-like "
            "town: Fountain (plaza), Bakery (market: buy/sell), Library, Docks, "
            "Tavern (evenings), Residential Zone (cottages & sleeping), Gym, and "
            "Workplace (offices).\n\n"
            f"Your job: {you.get('job', 'Resident')} — you can only `work` at the "
            f"{you.get('job_site', 'workplace')}.\n"
            f"Stats: Energy={you.get('energy')}/100, "
            f"Strength={you.get('strength')}/100, Coins={you.get('wealth')}, "
            f"House={you.get('house_id') or 'none (a cottage costs 30 coins)'}\n"
            f"Inventory: {json.dumps(inventory)}\n\n"
            f"Actions you can take via {_ACT_TOOL_NAME}:\n"
            "- stay: linger in place\n"
            "- move (place): walk to another location\n"
            "- say (text) / emote (text): converse or act\n"
            "- work: earn 10 coins + produce goods (at your job site, -15 energy)\n"
            "- workout: gain strength (at the gym, -10 energy)\n"
            "- buy: buy bread for 5 coins (at the bakery)\n"
            "- sell: sell one inventory item for 4 coins (at the bakery)\n"
            "- eat: eat a bread from your inventory (+25 energy, anywhere)\n"
            "- rest: sleep (+50 energy in your own house, +20 anywhere else)\n"
            "- buy_house: claim a free cottage lot (in the residential_zone, "
            "30 coins) — then you rest and sleep at home\n\n"
            f"Your personality:\n{persona or 'An easygoing, curious townsfolk.'}"
        )
        user_parts = [f"Observation:\n{json.dumps(tick)}"]
        if self._memory:
            user_parts.append("You remember:\n- " + "\n- ".join(self._memory))
        if whisper:
            user_parts.append(f"Your human whispers to you: {whisper!r}")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]
        result = await self._chat(messages, [_act_tool(tick.get("places") or [])])
        for call in getattr(result, "tool_calls", []) or []:
            if call.name == _ACT_TOOL_NAME:
                return _validate(call.arguments, tick.get("places") or [])
        return None

    async def _chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Any:
        if self._chat_fn is not None:
            return await self._chat_fn(messages, tools)

        # Real provider path (same plumbing as AgentPolicy).
        import httpx

        from backend.modules.agent import providers as P
        from backend.modules.agent.routes import _load_config

        config = _load_config()
        if config is None:
            raise RuntimeError("no agent provider configured")
        info = P.provider_for(config.provider)
        endpoint = config.endpoint or info.default_endpoint
        async with httpx.AsyncClient(timeout=45.0) as client:
            return await P.chat(client, info, endpoint, config.model, messages, tools)


def _act_tool(places: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": _ACT_TOOL_NAME,
            "description": "Commit this tick's single action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_ACTIONS),
                    },
                    "place": {
                        "type": "string",
                        "enum": list(places) or ["fountain"],
                        "description": "destination, for action=move",
                    },
                    "text": {
                        "type": "string",
                        "description": "what you say (say) or do (emote)",
                    },
                },
                "required": ["action"],
            },
        },
    }


def _validate(args: dict[str, Any], places: list[str]) -> dict[str, Any] | None:
    action = str(args.get("action") or "")
    if action not in _ACTIONS:
        return None
    out: dict[str, Any] = {"action": action}
    if action == "move":
        place = str(args.get("place") or "")
        if place not in places:
            return None
        out["place"] = place
    if action in ("say", "emote"):
        text = str(args.get("text") or "").strip()
        if not text:
            return None
        out["text"] = text
    return out
