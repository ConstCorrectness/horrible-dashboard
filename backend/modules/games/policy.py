"""How a node decides its move when the server says `your_turn`.

The server hands us an `observation` and the list of `legal_actions`; a `Policy`
returns the `id` of one of them. This is the seam where "the agent plays" lives:

- `RandomPolicy` — picks uniformly at random. No model needed, so the pipeline is
  watchable end-to-end even with no local LLM running.
- `AgentPolicy` — runs a real **tool-calling loop** with the player's *harness*
  (their [loadout](loadout.py)): the agent may call the player's custom tools to
  reason, then must call `game.chooseAction` with a legal id. The engine constrains
  the agent to legal moves; a better harness makes a better player. Any failure
  (provider down, no/illegal choice) falls back to a random legal move so a table
  never hangs.

Which one runs is the `games.policy` setting (`"random"` | `"agent"`).
"""

from __future__ import annotations

import json
import logging
import random
from typing import Any, Awaitable, Callable, Protocol

from backend.modules.games.loadout import HarnessRuntime, Loadout, get_loadout

logger = logging.getLogger(__name__)

_CHOOSE_TOOL_NAME = "game.chooseAction"
# How many harness rounds (custom-tool calls) before the agent must commit a move.
MAX_HARNESS_ROUNDS = 6


class Policy(Protocol):
    async def choose(
        self,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        game_id: str | None = None,
        seat: int | None = None,
    ) -> str: ...


def _ids(legal_actions: list[dict[str, Any]]) -> list[str]:
    return [str(a.get("id")) for a in legal_actions]


class RandomPolicy:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()

    async def choose(
        self,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        game_id: str | None = None,
        seat: int | None = None,
    ) -> str:
        return self._rng.choice(_ids(legal_actions))


# A chat function: (messages, tools) -> a ChatResult-like object with
# `.tool_calls` (each .name/.arguments/.id), `.assistant_message` (dict), `.content`.
ChatFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Awaitable[Any]]
# Resolve the loadout for a game (injectable for tests).
LoadoutFn = Callable[[str], Loadout]
# Receives one reasoning step (see the `kind` values emitted in `_drive`). Live UI
# and replay uploads hang off this; a None sink means "think silently" as before.
TraceFn = Callable[[dict[str, Any]], Any]

# Keep any single traced value (tool result, assistant text) readable, not huge.
_TRACE_VALUE_CHARS = 2_000


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text[:_TRACE_VALUE_CHARS]


class AgentPolicy:
    """Drives the local model through the player's harness to pick a move.

    `chat_fn`/`load_loadout` are injectable so the loop can be tested without a live
    provider or on-disk loadouts.
    """

    def __init__(
        self,
        fallback: Policy | None = None,
        *,
        chat_fn: ChatFn | None = None,
        load_loadout: LoadoutFn | None = None,
        trace: TraceFn | None = None,
    ) -> None:
        self._fallback = fallback or RandomPolicy()
        self._chat_fn = chat_fn
        self._load_loadout = load_loadout or get_loadout
        self._trace = trace

    def _emit(self, kind: str, **fields: Any) -> None:
        """Push one reasoning step to the trace sink; a broken sink never breaks
        the move."""
        if self._trace is None:
            return
        try:
            self._trace({"kind": kind, **fields})
        except Exception:
            logger.debug("trace sink failed", exc_info=True)

    async def choose(
        self,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        game_id: str | None = None,
        seat: int | None = None,
    ) -> str:
        ids = _ids(legal_actions)
        try:
            chosen = await self._run(observation, legal_actions, ids, game_id)
        except Exception:  # provider/harness failure — keep the game moving
            logger.debug("AgentPolicy failed; using fallback", exc_info=True)
            chosen = None
        if chosen in ids:
            return chosen  # type: ignore[return-value]
        fallback = await self._fallback.choose(observation, legal_actions, game_id)
        self._emit("fallback", action_id=fallback)
        return fallback

    async def run_once(
        self,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        game_id: str | None = None,
        seat: int | None = None,
    ) -> str | None:
        """One full harness drive with NO random fallback and no exception
        swallowing — the dry-run tester's entry point (see dryrun.py). Returns the
        committed action id, or None when the agent never committed."""
        return await self._run(observation, legal_actions, _ids(legal_actions), game_id)

    async def _run(
        self,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        ids: list[str],
        game_id: str | None,
    ) -> str | None:
        key = game_id or str(observation.get("game") or "default")
        loadout = self._load_loadout(key)
        runtime = HarnessRuntime(loadout)
        agent_code = (getattr(loadout, "agent_code", "") or "").strip()

        if self._chat_fn is not None:
            return await self._decide(
                self._chat_fn,
                runtime,
                loadout,
                agent_code,
                observation,
                legal_actions,
                ids,
            )

        import httpx

        # The loadout's own model (part of the harness — see model_config.py)
        # takes precedence; without one, borrow the agent module's configured model.
        from backend.modules.games import model_client, model_config

        loadout_model = model_config.parse_model(loadout.model)
        if loadout_model is not None:
            async with httpx.AsyncClient(
                timeout=45.0, headers=model_client.headers_for(loadout_model)
            ) as client:

                async def loadout_chat(
                    messages: list[dict[str, Any]], tools: list[dict[str, Any]]
                ) -> Any:
                    return await model_client.chat(
                        client, loadout_model, messages, tools
                    )

                return await self._decide(
                    loadout_chat,
                    runtime,
                    loadout,
                    agent_code,
                    observation,
                    legal_actions,
                    ids,
                )

        from backend.modules.agent import providers as P
        from backend.modules.agent.routes import _load_config

        config = _load_config()
        if config is None:
            # No model anywhere. A code-first agent that never calls config.decide()
            # (e.g. a pure-Python bot) can still run; the default agent cannot.
            if agent_code:
                return await self._decide(
                    None, runtime, loadout, agent_code, observation, legal_actions, ids
                )
            return None
        info = P.provider_for(config.provider)
        endpoint = config.endpoint or info.default_endpoint
        async with httpx.AsyncClient(timeout=45.0) as client:

            async def chat(
                messages: list[dict[str, Any]], tools: list[dict[str, Any]]
            ) -> Any:
                return await P.chat(
                    client, info, endpoint, config.model, messages, tools
                )

            return await self._decide(
                chat, runtime, loadout, agent_code, observation, legal_actions, ids
            )

    async def _decide(
        self,
        chat: ChatFn | None,
        runtime: HarnessRuntime,
        loadout: Loadout,
        agent_code: str,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        ids: list[str],
    ) -> str | None:
        """Run the player's agent. Empty `agent_code` ⇒ the default agent = the
        declarative context+tools model loop (`_drive`). Custom code ⇒ compile and call
        `my_agent(obs, config)`, where `config.decide(obs)` re-enters that same loop."""

        async def run_declarative(obs: dict[str, Any]) -> str | None:
            if chat is None:
                raise RuntimeError(
                    "this agent called config.decide() but no model is configured — "
                    "set one in the Model section, or return a move without the model"
                )
            return await self._drive(chat, runtime, obs, legal_actions, ids)

        if not agent_code:
            return None if chat is None else await run_declarative(observation)

        from backend.modules.games.agent_sdk import (
            AgentConfig,
            coerce_action_id,
            compile_agent,
        )

        my_agent = compile_agent(agent_code)  # raises on bad code (surfaced by dry-run)
        cfg = AgentConfig(
            loadout=loadout,
            legal_actions=legal_actions,
            ids=ids,
            run_declarative=run_declarative,
            emit=self._emit,
        )
        self._emit("assistant", content="Running my_agent(obs, config)…", tool_calls=[])
        # Ensure the agent can read legal moves off the observation (as BotPolicy does);
        # config.legal_actions is the same list.
        obs_for_agent = dict(observation)
        obs_for_agent.setdefault("legal_actions", legal_actions)
        result = my_agent(obs_for_agent, cfg)
        if hasattr(result, "__await__"):
            result = await result
        chosen = coerce_action_id(result, ids)
        if chosen is not None:
            self._emit("chose", action_id=chosen)
        return chosen

    async def _drive(
        self,
        chat: ChatFn,
        runtime: HarnessRuntime,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        ids: list[str],
    ) -> str | None:
        tools = runtime.provider_tools() + [_choose_tool(ids)]
        menu = "\n".join(
            f"- {a['id']}: {a.get('label', a['id'])}" for a in legal_actions
        )
        helper_line = (
            "You may first call your helper tools to analyze the position. "
            if runtime.provider_tools()
            else ""
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are an agent playing a turn-based game. "
                    f"{helper_line}"
                    f"When ready, call {_CHOOSE_TOOL_NAME} with the id of your chosen "
                    "legal action.\n\n" + runtime.loadout.context
                ).strip(),
            },
            {
                "role": "user",
                "content": (
                    f"Observation:\n{json.dumps(observation)}\n\n"
                    f"Legal actions:\n{menu}\n\nChoose one."
                ),
            },
        ]

        for _ in range(MAX_HARNESS_ROUNDS):
            result = await chat(messages, tools)
            messages.append(
                getattr(result, "assistant_message", None)
                or {"role": "assistant", "content": getattr(result, "content", "")}
            )
            calls = list(getattr(result, "tool_calls", []) or [])
            self._emit(
                "assistant",
                content=_clip(getattr(result, "content", "") or ""),
                tool_calls=[
                    {"name": c.name, "arguments": _clip(c.arguments)} for c in calls
                ],
            )
            if not calls:
                chosen = _prose_id(getattr(result, "content", ""), ids)
                if chosen is not None:
                    self._emit("chose", action_id=chosen)
                return chosen

            committed: str | None = None
            for call in calls:
                if call.name == _CHOOSE_TOOL_NAME:
                    aid = str(call.arguments.get("action_id") or "")
                    if aid in ids:
                        committed = aid
                    else:
                        messages.append(
                            _tool_msg(call, {"error": "illegal action", "legal": ids})
                        )
                elif runtime.has(call.name):
                    res = await runtime.call(call.name, call.arguments, observation)
                    messages.append(_tool_msg(call, res))
                    self._emit("tool_result", name=call.name, result=_clip(res))
                else:
                    messages.append(
                        _tool_msg(call, {"error": f"unknown tool {call.name}"})
                    )
            if committed is not None:
                self._emit("chose", action_id=committed)
                return committed
        return None


def _choose_tool(ids: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": _CHOOSE_TOOL_NAME,
            "description": "Commit your move: choose one legal action by its id.",
            "parameters": {
                "type": "object",
                # Constrain the model to legal ids at the schema level.
                "properties": {"action_id": {"type": "string", "enum": ids}},
                "required": ["action_id"],
            },
        },
    }


def _tool_msg(call: Any, result: Any) -> dict[str, Any]:
    return {
        "role": "tool",
        "name": call.name,
        "tool_call_id": getattr(call, "id", call.name),
        "content": json.dumps(result, default=str),
    }


def _prose_id(content: str, ids: list[str]) -> str | None:
    """Some models answer in prose; accept a bare legal id if that's all they gave."""
    text = (content or "").strip()
    return text if text in ids else None


class BotPolicy:
    """Runs the harness's **script** seat: a Python policy, in the RL sense.

    The bot is the tool named `bot` (or the loadout's first tool if there is none).
    Three code shapes are accepted — `class Agent`, `def act(obs, info)`, and the
    legacy `def run(args, obs)` — resolved by `bot_sdk.compile_bot`; see that module
    for what each one is handed.

    The compiled bot is **cached for the life of this policy object**, which the
    client rebuilds at each match boundary. That is what lets a `class Agent` hold
    state across the turns of one match without leaking it into the next.

    Any failure (won't compile, raises, returns nothing legal) falls back to a random
    legal move so a table never hangs — the same bargain `AgentPolicy` makes. The
    reason is traced, so the Games Log says *why* rather than just playing oddly.
    """

    def __init__(
        self,
        trace: TraceFn | None = None,
        load_loadout: LoadoutFn | None = None,
        fallback: Policy | None = None,
    ) -> None:
        self._trace = trace
        self._load_loadout = load_loadout or get_loadout
        self._fallback = fallback or RandomPolicy()
        self._bot: Any = None
        self._bot_key: str | None = None
        self._started = False

    def _emit(self, kind: str, **fields: Any) -> None:
        if self._trace is None:
            return
        try:
            self._trace({"kind": kind, **fields})
        except Exception:
            pass

    async def choose(
        self,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        game_id: str | None = None,
        seat: int | None = None,
    ) -> str:
        ids = _ids(legal_actions)
        try:
            chosen = await self._run(observation, legal_actions, ids, game_id, seat)
        except Exception as exc:
            logger.debug("BotPolicy failed; using fallback", exc_info=True)
            self._emit("fallback_reason", error=str(exc))
            chosen = None

        if chosen in ids:
            return chosen  # type: ignore[return-value]

        fallback = await self._fallback.choose(
            observation, legal_actions, game_id, seat
        )
        self._emit("fallback", action_id=fallback)
        return fallback

    def _resolve_bot(self, key: str) -> Any:
        """Compile (once per match) the loadout's bot tool.

        `bot_tool_of` always returns something — `<game>.bot`, `bot`, or the default
        random-legal-move baseline. It used to fall back to the loadout's *first*
        tool, which is normally a helper returning analysis rather than a move: that
        answered illegally every turn and degraded to random anyway, silently, in
        ranked matches too.
        """
        from backend.modules.games.bot_sdk import compile_bot
        from backend.modules.games.loadout import bot_tool_of

        if self._bot is not None and self._bot_key == key:
            return self._bot

        loadout = self._load_loadout(key)
        bot_tool = bot_tool_of(loadout, key)
        try:
            bot = compile_bot(bot_tool.code, f"<loadout:{bot_tool.name}>")
        except Exception as exc:
            raise ValueError(
                f"Bot script {bot_tool.name!r} failed to load: {exc}"
            ) from exc

        self._bot, self._bot_key, self._started = bot, key, False
        self._emit(
            "assistant",
            content=(
                f"Running bot script {bot_tool.name!r} "
                f"({'legacy run()' if bot.legacy else bot.shape} shape)"
            ),
            tool_calls=[],
        )
        return bot

    async def _run(
        self,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        ids: list[str],
        game_id: str | None,
        seat: int | None,
    ) -> str | None:
        from backend.games_engine.env_adapter import adapter_for
        from backend.modules.games.bot_sdk import build_info, coerce_action

        key = game_id or str(observation.get("game") or "default")
        bot = self._resolve_bot(key)

        # The legacy shape read legal moves off the observation; keep doing that so
        # existing bots see exactly what they always did.
        obs_for_bot = dict(observation)
        obs_for_bot.setdefault("legal_actions", legal_actions)
        info = build_info(obs_for_bot, legal_actions, key, seat)

        if not self._started:
            self._started = True
            bot.reset(obs_for_bot, info)

        result = bot.act(obs_for_bot, info)
        if hasattr(result, "__await__"):
            result = await result
        self._emit("tool_result", name="bot", result=_clip(result))

        chosen = coerce_action(result, ids, adapter_for(key))
        if chosen is None:
            raise ValueError(
                f"bot returned {result!r}, which is not one of the legal actions {ids}"
            )
        self._emit("chose", action_id=chosen)
        return chosen


def make_policy(name: str, trace: TraceFn | None = None) -> Policy:
    if name == "agent":
        return AgentPolicy(trace=trace)
    elif name == "bot":
        return BotPolicy(trace=trace)
    else:
        return RandomPolicy()
