"""The **script** seat's contract: a policy, in the reinforcement-learning sense.

A script bot is now the same object every RL library already understands — a thing
with an `act`. `backend/games_engine/env.py` is the environment you train it
against; this is what plays it. Train in the Env, ship the `act` to the ladder.

Three shapes are accepted, resolved **by inspection, in this order**:

1. `class Agent` with `act(self, obs, info)` — the full contract. Instantiated once
   per match, so it may hold state across turns, and it receives `reset(obs, info)`
   at the start and `observe(reward, terminated, info)` at the end.
2. `def act(obs, info)` — a stateless policy function, for a bot that is a pure
   mapping from observation to move.
3. `def run(args, obs)` — **the legacy shape**, still fully supported. Every shipped
   template used it and people have saved harnesses written against it; breaking
   those to tidy up an interface would be a poor trade. `args` was always `{}` and
   is passed as such.

Shapes 1 and 2 get `info`, which is where everything the old contract lacked lives:
`action_mask` (the fixed-width legality vector, same field name masked-PPO looks
for), the encoded seat-relative `obs` array, `legal_actions`, and the seat. Shape 3
gets none of it — that is the actual reason to upgrade, not style.

**Return values** may be an action id string, or an integer index into the game's
action space (what a trained network emits). The index is only meaningful when the
game has an adapter, so `coerce_action` refuses to guess otherwise rather than
silently reinterpreting an int as an id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from backend.games_engine.env_adapter import EnvAdapter, adapter_for

logger = logging.getLogger(__name__)


class BotShapeError(ValueError):
    """The code compiled but defines none of the three accepted entry points."""


@dataclass
class CompiledBot:
    """A compiled script bot, normalised to one interface regardless of shape."""

    shape: str  # "agent" | "act" | "run" (legacy)
    _act: Callable[..., Any]
    _instance: Any = None

    @property
    def legacy(self) -> bool:
        return self.shape == "run"

    def reset(self, obs: dict[str, Any], info: dict[str, Any]) -> None:
        hook = getattr(self._instance, "reset", None)
        if callable(hook):
            hook(obs, info)

    def observe(self, reward: float, terminated: bool, info: dict[str, Any]) -> None:
        hook = getattr(self._instance, "observe", None)
        if callable(hook):
            hook(reward, terminated, info)

    def act(self, obs: dict[str, Any], info: dict[str, Any]) -> Any:
        if self.shape == "run":
            return self._act({}, obs)
        return self._act(obs, info)


def compile_bot(code: str, filename: str = "<bot>") -> CompiledBot:
    """Exec a bot body and resolve its entry point. Trusted (the author's own code),
    same as every other harness tool."""
    namespace: dict[str, Any] = {}
    exec(compile(code, filename, "exec"), namespace)  # noqa: S102 (trusted)

    agent_cls = namespace.get("Agent")
    if isinstance(agent_cls, type) and callable(getattr(agent_cls, "act", None)):
        instance = agent_cls()
        return CompiledBot(shape="agent", _act=instance.act, _instance=instance)

    act = namespace.get("act")
    if callable(act):
        return CompiledBot(shape="act", _act=act)

    run = namespace.get("run")
    if callable(run):
        return CompiledBot(shape="run", _act=run)

    raise BotShapeError(
        "bot code must define `class Agent` with an `act(self, obs, info)` method, "
        "a function `act(obs, info)`, or the legacy `run(args, obs)`"
    )


def build_info(
    observation: dict[str, Any],
    legal_actions: list[dict[str, Any]],
    game_id: str | None,
    seat: int | None,
) -> dict[str, Any]:
    """The `info` dict handed to a modern bot — deliberately the **same keys** the
    Env puts in its own `info`, so a policy written against the Env runs unchanged
    in a live match. That equivalence is the whole point of the contract; if these
    two ever drift, code that trained fine will mysteriously misbehave on the ladder.

    `obs` (the encoded array) and `action_mask` are absent for a game with no
    adapter — a reasoner game has no action space — so a bot that wants them must
    tolerate their absence, which `info.get` makes natural.
    """
    info: dict[str, Any] = {
        "legal_actions": legal_actions,
        "raw_obs": observation,
        "seat": seat,
        "game_id": game_id,
    }
    adapter = adapter_for(game_id) if game_id else None
    if adapter is not None:
        info["action_mask"] = adapter.mask_for(legal_actions)
        if seat is not None:
            info["obs"] = adapter.encode_obs(observation, seat)
    return info


def coerce_action(
    result: Any, legal_ids: list[str], adapter: EnvAdapter | None
) -> str | None:
    """Turn whatever the bot returned into a legal action id, or None.

    Order matters. A bool is checked before int because `True` is `1` in Python and
    would otherwise silently mean "action 1". An integer is only read as a space
    index when the game *has* a space; without one, an int is treated as an id
    string, which is what the legacy contract meant by returning `3`.
    """
    if result is None or isinstance(result, bool):
        return None

    if isinstance(result, dict):
        for key in ("action", "action_id", "id"):
            if key in result:
                return coerce_action(result[key], legal_ids, adapter)
        return None

    # numpy scalars arrive from any trained policy; `.item()` unwraps them.
    item = getattr(result, "item", None)
    if callable(item) and not isinstance(result, (str, bytes)):
        try:
            result = item()
        except (ValueError, TypeError):
            return None

    if isinstance(result, (int,)) and not isinstance(result, bool):
        if adapter is not None:
            try:
                candidate = adapter.to_action_id(int(result))
            except (ValueError, KeyError):
                return None
            return candidate if candidate in legal_ids else None
        return str(result) if str(result) in legal_ids else None

    if isinstance(result, str):
        return result if result in legal_ids else None

    return None
