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

# Synthetic version id for a game running its **shipped starter harness** (the
# seeded default — no saved loadout yet). It isn't persisted; the first real save
# branches to `v1` and the starter disappears. Surfaced so the ladder/replay can
# attribute a default-harness match to *something* instead of a blank version.
STARTER_VERSION = "starter"


def _has_starter(game_id: str) -> bool:
    """Whether `game_id` ships a starter template (so its seeded default plays)."""
    from backend.modules.games.templates import default_loadout_for

    return default_loadout_for(game_id) is not None


# The commit tool's namespace; player tools must not shadow it.
RESERVED_TOOL_PREFIX = "game."


def tool_name_error(name: str, taken: set[str] | None = None) -> str | None:
    """Why `name` is not a valid tool name, or None if it is. `taken` holds the
    names already used by other tools in the same loadout (duplicate check).
    Dots stay legal (the shipped `fighter.bot` template uses one); only the
    `game.` prefix is reserved."""
    import re

    if not name:
        return "tool name is empty"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
        return "tool name must start with a letter or _ and use only letters, digits, _ . -"
    if name.startswith(RESERVED_TOOL_PREFIX):
        return (
            f"the {RESERVED_TOOL_PREFIX}* namespace is reserved for built-in game tools"
        )
    if taken is not None and name in taken:
        return f"duplicate tool name {name!r}"
    return None


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
    # ModelConfig-shaped dict (provider/model/endpoint/api_key_name) or None to
    # borrow the agent module's configured model. See model_config.py.
    model: dict[str, Any] | None = None
    # Optional `my_agent(obs, config)` entrypoint (agent_sdk.py). Empty ⇒ the default
    # agent = this loadout's context+tools driving the model. The code-first top layer.
    agent_code: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "context": self.context,
            "tools": [t.to_wire() for t in self.tools],
            "model": self.model,
            "agent_code": self.agent_code,
        }

    @classmethod
    def from_wire(cls, game_id: str, d: dict[str, Any]) -> "Loadout":
        model = d.get("model")
        return cls(
            game_id=game_id,
            context=str(d.get("context") or ""),
            tools=[ToolDef.from_wire(t) for t in (d.get("tools") or [])],
            model=dict(model) if isinstance(model, dict) else None,
            agent_code=str(d.get("agent_code") or ""),
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


# ---- versioning --------------------------------------------------------------
#
# On-disk shape v2, per game: {"active": vid, "versions": {vid: {label, created_at,
# context, tools, model}}}. The v1 shape (a bare loadout dict) upgrades in memory
# on read and is rewritten as v2 on the first save — so a fresh checkout and a
# years-old loadouts file both just work. Versions are the harness-progression
# loop: play → study the replay → branch the loadout → requeue.


def _as_v2(entry: Any) -> dict[str, Any]:
    if isinstance(entry, dict) and "versions" in entry:
        return entry
    body = dict(entry) if isinstance(entry, dict) else {}
    body.setdefault("label", "v1")
    body.setdefault("created_at", 0.0)
    return {"active": "v1", "versions": {"v1": body}}


def _entry_for(data: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    if game_id in data:
        return _as_v2(data[game_id])
    if DEFAULT_KEY in data:
        return _as_v2(data[DEFAULT_KEY])
    return None


def _active_body(entry: dict[str, Any]) -> dict[str, Any]:
    versions = entry.get("versions") or {}
    active = entry.get("active")
    if active in versions:
        return versions[active]
    return next(iter(versions.values()), {})


# ---- the default bot tool ----------------------------------------------------

# The script seat's baseline: a uniformly random legal move.
#
# Every harness has one, always. Before this, a loadout with no `bot` tool made
# `BotPolicy` fall back to running the loadout's **first** tool — which is normally
# a *helper* (`board_scanner` returns `{"win_at": ..., "block_at": ...}`), not a
# move. That answered illegally every single turn and silently degraded to a random
# move anyway, in ranked matches included. Guaranteeing the tool exists removes the
# guess: the fallback and the baseline are now the same visible, editable thing.
#
# Written in the modern `act(obs, info)` shape so a new player's first sight of the
# contract is the contract, and reading `info["legal_actions"]` rather than the
# action mask so it works for every game — including reasoner games, which have no
# action space at all.
DEFAULT_BOT_TOOL_NAME = "bot"

DEFAULT_BOT_CODE = '''\
import random


def act(obs, info):
    """Pick a uniformly random legal move.

    This is the baseline every policy is measured against — beating it is the
    first thing a real bot has to do. Replace the body with your own logic:

        info["legal_actions"]  every move you may make right now
        info["action_mask"]    same thing as a 0/1 vector (games with an RL env)
        info["obs"]            the encoded, seat-relative observation
        obs                    the raw observation dict

    Return an action id, or an integer index into the action space. For a bot that
    remembers things between turns, use `class Agent` with an `act(self, obs, info)`
    method instead — it also gets `reset()` and `observe(reward, terminated, info)`.
    """
    return random.choice(info["legal_actions"])["id"]
'''


def default_bot_tool() -> ToolDef:
    return ToolDef(
        name=DEFAULT_BOT_TOOL_NAME,
        description="Your script seat: picks a move from the legal set.",
        code=DEFAULT_BOT_CODE,
    )


def bot_tool_of(loadout: Loadout, game_id: str | None = None) -> ToolDef:
    """The tool the **script seat** runs.

    Resolution is `<game_id>.bot` → `bot` → the default random bot. The per-game
    name is not decoration: shipped harnesses use it (`fighter.bot`) and the Build
    panel has always resolved it, while the policy only ever looked for `bot` — so
    a fighter harness ran whichever tool happened to be first. The two now agree.

    Never returns None and never falls through to "some other tool": a helper that
    returns analysis is not a move, and running one as though it were is exactly the
    failure this replaces.
    """
    by_name = {t.name: t for t in loadout.tools}
    if game_id and f"{game_id}.bot" in by_name:
        return by_name[f"{game_id}.bot"]
    if DEFAULT_BOT_TOOL_NAME in by_name:
        return by_name[DEFAULT_BOT_TOOL_NAME]
    return default_bot_tool()


def _with_default_bot(loadout: Loadout) -> Loadout:
    """Guarantee a bot tool on every loadout that leaves this module.

    Injected on **read** rather than written into storage, so existing saved
    harnesses gain it with no migration and a player who deletes it gets it back
    rather than silently losing their script seat.
    """
    names = {t.name for t in loadout.tools}
    if DEFAULT_BOT_TOOL_NAME in names or f"{loadout.game_id}.bot" in names:
        return loadout
    loadout.tools.append(default_bot_tool())
    return loadout


def get_loadout(game_id: str) -> Loadout:
    """The **active version** of the loadout for `game_id`, falling back to the
    user's `default` loadout and, failing that, to the game's **shipped starter
    harness** so a fresh player's agent already has a working default.

    Whatever the source, the result carries a bot tool (see `_with_default_bot`)."""
    entry = _entry_for(_read_all(), game_id)
    if entry is None:
        # Lazy import avoids a module-load cycle (templates is imported by routes).
        from backend.modules.games.templates import default_loadout_for

        body = default_loadout_for(game_id)
        if body is not None:
            return _with_default_bot(Loadout.from_wire(game_id, body))
        return _with_default_bot(Loadout(game_id=game_id))
    return _with_default_bot(Loadout.from_wire(game_id, _active_body(entry)))


def active_version_id(game_id: str) -> str | None:
    """Which version would play right now (for match attribution). With no saved
    loadout, returns the synthetic `STARTER_VERSION` when the game ships a starter
    (its seeded default plays), else None."""
    entry = _entry_for(_read_all(), game_id)
    if entry is None:
        return STARTER_VERSION if _has_starter(game_id) else None
    active = entry.get("active")
    return active if active in (entry.get("versions") or {}) else None


def save_loadout(loadout: Loadout) -> Loadout:
    """Overwrite the active version in place (the classic PUT path)."""
    import time

    data = _read_all()
    entry = _as_v2(data.get(loadout.game_id)) if loadout.game_id in data else None
    if entry is None:
        entry = {"active": "v1", "versions": {}}
        entry["versions"]["v1"] = {"label": "v1", "created_at": time.time()}
    body = entry["versions"].setdefault(
        entry["active"], {"label": entry["active"], "created_at": time.time()}
    )
    body.update(loadout.to_wire())
    body.pop("game_id", None)
    data[loadout.game_id] = entry
    _write_all(data)
    return loadout


def list_versions(game_id: str) -> list[dict[str, Any]]:
    """Version summaries for a game, newest first. With no saved loadout, a game that
    ships a starter lists one synthetic, active `starter` version (kept in sync with
    `active_version_id`)."""
    entry = _entry_for(_read_all(), game_id)
    if entry is None:
        if _has_starter(game_id):
            return [
                {
                    "id": STARTER_VERSION,
                    "label": "starter",
                    "created_at": 0.0,
                    "active": True,
                    "model": None,
                }
            ]
        return []
    versions = entry.get("versions") or {}
    return sorted(
        (
            {
                "id": vid,
                "label": str(body.get("label") or vid),
                "created_at": float(body.get("created_at") or 0.0),
                "active": vid == entry.get("active"),
                "model": body.get("model"),
            }
            for vid, body in versions.items()
        ),
        key=lambda v: v["created_at"],
        reverse=True,
    )


def save_version(game_id: str, loadout: Loadout, label: str = "") -> str:
    """Branch: save `loadout` as a NEW version of `game_id` and make it active."""
    import time

    data = _read_all()
    entry = (
        _as_v2(data.get(game_id)) if game_id in data else {"active": "", "versions": {}}
    )
    versions = entry["versions"]
    n = 1
    while f"v{n}" in versions:
        n += 1
    vid = f"v{n}"
    body = loadout.to_wire()
    body.pop("game_id", None)
    body["label"] = label or vid
    body["created_at"] = time.time()
    versions[vid] = body
    entry["active"] = vid
    data[game_id] = entry
    _write_all(data)
    return vid


def activate_version(game_id: str, version_id: str) -> bool:
    data = _read_all()
    if game_id not in data:
        return False
    entry = _as_v2(data[game_id])
    if version_id not in (entry.get("versions") or {}):
        return False
    entry["active"] = version_id
    data[game_id] = entry
    _write_all(data)
    return True


def delete_version(game_id: str, version_id: str) -> bool:
    """Delete a version (never the last one; deleting the active one activates the
    newest remaining)."""
    data = _read_all()
    if game_id not in data:
        return False
    entry = _as_v2(data[game_id])
    versions = entry.get("versions") or {}
    if version_id not in versions or len(versions) <= 1:
        return False
    del versions[version_id]
    if entry.get("active") == version_id:
        newest = max(
            versions, key=lambda vid: float(versions[vid].get("created_at") or 0.0)
        )
        entry["active"] = newest
    data[game_id] = entry
    _write_all(data)
    return True


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

    def compile_errors(self) -> dict[str, str]:
        """Every tool that failed to compile, name → error (for diagnostics)."""
        return dict(self._errors)

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
