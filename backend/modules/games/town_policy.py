"""The node-side mind of an AgentTown resident.

The server owns the world; **this** owns the fish. Each `town_tick` observation
lands here and one action goes back (`stay`/`move`/`say`/`emote`):

- **Routine mode** (no model, `games.policy` = random/manual, or any failure) —
  the canned life that keeps the tank alive: mostly linger, wander sometimes,
  greet whoever's around, doze through the night phase. The skill floor.
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
        you = tick.get("you") or {}
        place = str(you.get("place") or "fountain")
        phase = str(tick.get("phase") or "morning")
        places = [p for p in (tick.get("places") or []) if p != place]
        occupants = list(tick.get("occupants") or [])

        # Doze through the night like a sensible fish.
        if phase == "night":
            return {"action": "emote", "text": "dozes off under the stars"}
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
            return {"action": "move", "place": self._rng.choice(places)}
        if roll < 0.45:
            return {"action": "emote", "text": self._rng.choice(_EMOTES)}
        return {"action": "stay"}

    # ---- agent mode -------------------------------------------------------------

    async def _agent(self, tick: dict[str, Any]) -> dict[str, Any] | None:
        from backend.modules.games.loadout import get_loadout

        persona = (get_loadout("town").context or "").strip()
        you = tick.get("you") or {}
        whisper, self._whisper = self._whisper, None  # a nudge is spent on use

        system = (
            f"You are {you.get('name')}, a resident of AgentTown — a small town "
            "with a fountain, bakery, tavern, library, and docks. You live one "
            "slow tick at a time; each tick you take exactly one action by "
            f"calling {_ACT_TOOL_NAME}. Stay in character, keep sayings short "
            "and social, and let your personality show.\n\n"
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
                        "enum": ["stay", "move", "say", "emote"],
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
    if action not in ("stay", "move", "say", "emote"):
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
