"""The **agent harness** a player engineers for a game — the heart of the skill game.

A loadout is a player's *context* (strategy/system prompt) plus a set of **custom
tools they author as real Python** that their agent can call while deciding a move.
The better your tools (and context), the better your agent plays — so the human's
skill is in tool/harness engineering, not in moving pieces.

Trust model: a loadout runs **only on its author's own node**, and the tools only
ever see the observation the server sent *this* seat — they physically cannot touch
an opponent's hidden state. So, like the backend plugin SDK, tool code is trusted
and unsandboxed in v1 (it's your own code on your own machine, no different from
editing the app). Each tool body defines `run(args, obs)`:

    def run(args, obs):
        # args: the model's arguments; obs: this seat's observation
        return {"winning_cell": ...}

See docs/modules/games.mdx (agent harness).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Applies to any game when no game-specific loadout exists.
DEFAULT_KEY = "default"


@dataclass
class ToolDef:
    name: str
    description: str
    code: str  # must define `run(args, obs)`
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)

    def provider_tool(self) -> dict[str, Any]:
        """The OpenAI/Ollama tool schema advertised to the model."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": dict(self.parameters),
                    "required": list(self.required),
                },
            },
        }

    def to_wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "code": self.code,
            "parameters": self.parameters,
            "required": self.required,
        }

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> "ToolDef":
        return cls(
            name=str(d.get("name") or ""),
            description=str(d.get("description") or ""),
            code=str(d.get("code") or ""),
            parameters=dict(d.get("parameters") or {}),
            required=list(d.get("required") or []),
        )


@dataclass
class Loadout:
    game_id: str
    context: str = ""
    tools: list[ToolDef] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "context": self.context,
            "tools": [t.to_wire() for t in self.tools],
        }

    @classmethod
    def from_wire(cls, game_id: str, d: dict[str, Any]) -> "Loadout":
        return cls(
            game_id=game_id,
            context=str(d.get("context") or ""),
            tools=[ToolDef.from_wire(t) for t in (d.get("tools") or [])],
        )


# ---- persistence -----------------------------------------------------------


def _store_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "games_loadouts.json"


def _read_all() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        logger.warning("games loadouts file is corrupt; starting empty")
        return {}


def _write_all(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_loadout(game_id: str) -> Loadout:
    """The loadout for `game_id`, falling back to the `default` loadout."""
    data = _read_all()
    if game_id in data:
        return Loadout.from_wire(game_id, data[game_id])
    if DEFAULT_KEY in data:
        return Loadout.from_wire(game_id, data[DEFAULT_KEY])
    return Loadout(game_id=game_id)


def save_loadout(loadout: Loadout) -> Loadout:
    data = _read_all()
    data[loadout.game_id] = loadout.to_wire()
    _write_all(data)
    return loadout


# ---- runtime ---------------------------------------------------------------


class HarnessRuntime:
    """Compiles a loadout's tools once and invokes them, capturing errors so a
    buggy tool degrades the agent's play (its own problem) rather than crashing the
    turn."""

    def __init__(self, loadout: Loadout) -> None:
        self.loadout = loadout
        self._compiled: dict[str, Callable[..., Any]] = {}
        self._errors: dict[str, str] = {}
        for tool in loadout.tools:
            try:
                self._compiled[tool.name] = _compile_tool(tool.name, tool.code)
            except Exception as exc:  # a tool that won't even compile is just absent
                self._errors[tool.name] = str(exc)
                logger.info("loadout tool %r failed to compile: %s", tool.name, exc)

    def provider_tools(self) -> list[dict[str, Any]]:
        """Schemas for the tools that compiled (broken tools aren't advertised)."""
        return [
            t.provider_tool() for t in self.loadout.tools if t.name in self._compiled
        ]

    def has(self, name: str) -> bool:
        return name in self._compiled

    def compile_error(self, name: str) -> str | None:
        return self._errors.get(name)

    async def call(self, name: str, args: dict[str, Any], obs: dict[str, Any]) -> Any:
        fn = self._compiled.get(name)
        if fn is None:
            return {"error": self._errors.get(name, f"no such tool {name!r}")}
        try:
            result = fn(args, obs)
            if hasattr(result, "__await__"):
                result = await result
            # Ensure the result is JSON-serializable for the tool message.
            json.dumps(result, default=str)
            return result
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}


def _compile_tool(name: str, code: str) -> Callable[..., Any]:
    """Exec a tool body and return its `run` callable. Trusted (author's own code)."""
    namespace: dict[str, Any] = {}
    exec(compile(code, f"<loadout:{name}>", "exec"), namespace)  # noqa: S102 (trusted)
    fn = namespace.get("run")
    if not callable(fn):
        raise ValueError("tool code must define a callable `run(args, obs)`")
    return fn
