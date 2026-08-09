"""The **agent harness** a player engineers for a game — the heart of the skill game.

There are **two harnesses, and they are different objects**, because there are two
kinds of game (see `games_engine/base.py`):

- **`LlmHarness`** (kind `llm`) — the player's *context* (strategy/system prompt),
  a set of **custom tools they author as real Python** for the model to call, the
  `model` itself, and an optional `my_agent(obs, config)` entrypoint. This is the
  harness for a **reasoner** game, where the skill is context engineering.
- **`CodedHarness`** (kind `coded`) — one piece of Python, `bot_code`, mapping
  observation → action with no model anywhere in the loop. This is the harness for
  a **coded-agent** game, where the skill is writing the policy.

They are stored separately (see the v3 note below) and neither can hold the other's
fields. A game that takes the turn-based escape hatch (tic-tac-toe) may have one of
each; **which one plays is decided by the seat's move policy**, not by the game
alone, which is why `harness_kind` resolves through `_resolve_policy_name`.

Trust model: a harness runs **only on its author's own node**, and its code only
ever sees the observation the server sent *this* seat — it physically cannot touch
an opponent's hidden state. So, like the backend plugin SDK, the code is trusted and
unsandboxed in v1 (it's your own code on your own machine, no different from editing
the app). An LLM harness's tool body defines `run(args, obs)`:

    def run(args, obs):
        # args: the model's arguments; obs: this seat's observation
        return {"winning_cell": ...}

A coded harness's `bot_code` defines `act(obs, info)` (or `class Agent`, or the
legacy `run(args, obs)`) — see `bot_sdk.compile_bot`.

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

# The two harnesses. `coded` is `bot_code` alone; `llm` is context + tools + model
# + agent_code. Nothing holds both.
CODED = "coded"
LLM = "llm"
HARNESS_KINDS = (CODED, LLM)


def harness_kind_for_policy(policy: str) -> str:
    """Which harness a seat running `policy` would use. Only the `bot` policy is
    coded; `agent` is the LLM one. `random`/`manual` run neither, and resolve to
    the game's other side so the Build panel still shows something editable."""
    return CODED if policy == "bot" else LLM


def harness_kind(game_id: str) -> str:
    """The harness this node's seat would use for `game_id` **right now**.

    Resolved through the seat's effective move policy, not the game's category,
    because a turn-based coded game on the escape hatch (tic-tac-toe) can run
    either — and which one it runs is exactly what the player's driver choice
    decides. Uncatalogued ids (AgentTown's `town` persona) fall to `llm`.
    """
    from backend.modules.games.client import _resolve_policy_name

    return harness_kind_for_policy(_resolve_policy_name(game_id))


# Applies to any game when no game-specific harness exists.
DEFAULT_KEY = "default"

# Synthetic version id for a game running its **shipped starter harness** (the
# seeded default — no saved loadout yet). It isn't persisted; the first real save
# branches to `v1` and the starter disappears. Surfaced so the ladder/replay can
# attribute a default-harness match to *something* instead of a blank version.
STARTER_VERSION = "starter"


def _has_starter(game_id: str, kind: str) -> bool:
    """Whether `game_id` ships a starter template of `kind` (so its seeded default
    plays). A coded game always effectively has one — the random-legal baseline —
    but that is the *absence* of authored work, so it doesn't count as a starter."""
    from backend.modules.games.templates import default_harness_for

    return default_harness_for(game_id, kind) is not None


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
class LlmHarness:
    """The harness for an **LLM agent** seat: the model's context, the tools it may
    call, the model itself, and an optional `my_agent` entrypoint. Carries no
    `bot_code` — a coded policy is the other harness, not a field of this one."""

    kind = LLM

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
    def from_wire(cls, game_id: str, d: dict[str, Any]) -> "LlmHarness":
        model = d.get("model")
        return cls(
            game_id=game_id,
            context=str(d.get("context") or ""),
            tools=[ToolDef.from_wire(t) for t in (d.get("tools") or [])],
            model=dict(model) if isinstance(model, dict) else None,
            agent_code=str(d.get("agent_code") or ""),
        )


# The coded seat's baseline: a uniformly random legal move.
#
# Every coded harness has one, always — with no authored bot, this is what plays.
# It used to be injected as a *tool* named `bot` into the LLM harness's tool list,
# which is precisely the conflation this split removes: a policy is not a tool the
# model may call, and storing it as one meant `BotPolicy` had to hunt the tool list
# by name (`<game>.bot` → `bot` → …) and could pick up a helper that returns
# analysis rather than a move.
#
# Written in the modern `act(obs, info)` shape so a new player's first sight of the
# contract is the contract, and reading `info["legal_actions"]` rather than the
# action mask so it works for every game.
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


@dataclass
class CodedHarness:
    """The harness for a **coded agent** seat: one Python policy, no model.

    `bot_code` is never empty on the way out of this module — an unauthored harness
    reads back as `DEFAULT_BOT_CODE`, so "what will play" and "what the editor shows"
    are the same visible, editable thing.
    """

    kind = CODED

    game_id: str
    bot_code: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {"game_id": self.game_id, "bot_code": self.bot_code}

    @classmethod
    def from_wire(cls, game_id: str, d: dict[str, Any]) -> "CodedHarness":
        return cls(game_id=game_id, bot_code=str(d.get("bot_code") or ""))


Harness = LlmHarness | CodedHarness


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
# On-disk shape **v3**, per game: one side per harness kind, each with its own
# active pointer and version history:
#
#   {"<game>": {"coded": {"active": vid, "versions": {vid: {label, created_at,
#                                                           bot_code}}},
#               "llm":   {"active": vid, "versions": {vid: {label, created_at,
#                                                           context, tools, model,
#                                                           agent_code}}}}}
#
# Separate histories are the point: iterating a bot and iterating a prompt are
# different activities with different units of progress, and a hatch game may have
# both. Older shapes upgrade in memory on read and are rewritten on the first save,
# so a fresh checkout and a years-old file both just work:
#
#   v2  {"active": vid, "versions": {vid: {context, tools, model, ...}}}
#   v1  a bare loadout body
#
# Versions are the harness-progression loop: play → study the replay → branch →
# requeue.

# In v2 the coded policy lived *inside* the tool list, under `<game>.bot` or `bot`.
# The migration lifts it out; that name-hunt exists nowhere else now.
_LEGACY_BOT_NAMES = ("bot",)


def _is_legacy_bot_tool(name: str, game_id: str) -> bool:
    return name in _LEGACY_BOT_NAMES or name == f"{game_id}.bot"


def _as_v2(entry: Any) -> dict[str, Any]:
    if isinstance(entry, dict) and "versions" in entry:
        return entry
    body = dict(entry) if isinstance(entry, dict) else {}
    body.setdefault("label", "v1")
    body.setdefault("created_at", 0.0)
    return {"active": "v1", "versions": {"v1": body}}


def _as_v3(entry: Any, game_id: str) -> dict[str, Any]:
    """Upgrade any stored shape to v3. Splitting a v2 version yields one version on
    each side with the **same id**, so a player's history stays aligned across the
    split and nothing they authored is dropped: the bot tool becomes `bot_code`, and
    everything else stays on the LLM side."""
    if isinstance(entry, dict) and (CODED in entry or LLM in entry):
        return entry
    if not entry:
        # Nothing stored. Distinct from "a legacy body that happens to be sparse":
        # treating an empty dict as a v1 body invents a phantom `v1` on both sides,
        # which then shifts the player's first real save to `v2`.
        return {
            CODED: {"active": "", "versions": {}},
            LLM: {"active": "", "versions": {}},
        }
    v2 = _as_v2(entry)
    active = v2.get("active") or ""
    coded: dict[str, Any] = {}
    llm: dict[str, Any] = {}
    for vid, body in (v2.get("versions") or {}).items():
        meta = {
            "label": body.get("label") or vid,
            "created_at": float(body.get("created_at") or 0.0),
        }
        tools = [dict(t) for t in (body.get("tools") or [])]
        bot = next(
            (
                t
                for t in tools
                if _is_legacy_bot_tool(str(t.get("name") or ""), game_id)
            ),
            None,
        )
        coded[vid] = {**meta, "bot_code": str((bot or {}).get("code") or "")}
        llm[vid] = {
            **meta,
            "context": body.get("context") or "",
            "tools": [
                t
                for t in tools
                if not _is_legacy_bot_tool(str(t.get("name") or ""), game_id)
            ],
            "model": body.get("model"),
            "agent_code": body.get("agent_code") or "",
        }
    return {
        CODED: {"active": active, "versions": coded},
        LLM: {"active": active, "versions": llm},
    }


def _entry_for(data: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    if game_id in data:
        return _as_v3(data[game_id], game_id)
    if DEFAULT_KEY in data:
        return _as_v3(data[DEFAULT_KEY], game_id)
    return None


def _side(entry: dict[str, Any] | None, kind: str) -> dict[str, Any]:
    """One harness kind's `{active, versions}` block, or an empty one."""
    if entry is None:
        return {"active": "", "versions": {}}
    side = entry.get(kind)
    return side if isinstance(side, dict) else {"active": "", "versions": {}}


def _active_body(side: dict[str, Any]) -> dict[str, Any]:
    versions = side.get("versions") or {}
    active = side.get("active")
    if active in versions:
        return versions[active]
    return next(iter(versions.values()), {})


def _is_authored(body: dict[str, Any], kind: str) -> bool:
    """Whether a stored version holds anything the player actually wrote. The
    migration writes a version on *both* sides of every v2 entry, so a coded game's
    llm side (and vice versa) is normally empty — and an empty side must fall
    through to the shipped starter rather than shadowing it with nothing."""
    if not body:
        return False
    if kind == CODED:
        return bool(str(body.get("bot_code") or "").strip())
    return bool(
        str(body.get("context") or "").strip()
        or body.get("tools")
        or body.get("model")
        or str(body.get("agent_code") or "").strip()
    )


# ---- reading a harness --------------------------------------------------------


def _starter_body(game_id: str, kind: str) -> dict[str, Any] | None:
    # Lazy import avoids a module-load cycle (templates is imported by routes).
    from backend.modules.games.templates import default_harness_for

    return default_harness_for(game_id, kind)


def get_llm_harness(game_id: str) -> LlmHarness:
    """The **active version** of the LLM harness for `game_id`, falling back to the
    user's `default` harness and, failing that, to the game's **shipped starter** so
    a fresh player's agent already has a working one."""
    body = _active_body(_side(_entry_for(_read_all(), game_id), LLM))
    if not _is_authored(body, LLM):
        body = _starter_body(game_id, LLM) or {}
    return LlmHarness.from_wire(game_id, body)


def get_coded_harness(game_id: str) -> CodedHarness:
    """The **active version** of the coded harness for `game_id`, falling back to the
    shipped starter bot and finally to `DEFAULT_BOT_CODE`.

    Never returns empty code: the seat has to decide *something* every tick, and the
    random-legal baseline is what "I haven't written one yet" means."""
    body = _active_body(_side(_entry_for(_read_all(), game_id), CODED))
    if not _is_authored(body, CODED):
        body = _starter_body(game_id, CODED) or {}
    harness = CodedHarness.from_wire(game_id, body)
    if not harness.bot_code.strip():
        harness.bot_code = DEFAULT_BOT_CODE
    return harness


def get_harness(game_id: str, kind: str | None = None) -> Harness:
    """The harness of `kind` for a game — defaulting to whichever one this node's
    seat would actually run (see `harness_kind`)."""
    kind = kind or harness_kind(game_id)
    return get_coded_harness(game_id) if kind == CODED else get_llm_harness(game_id)


def active_version_id(game_id: str, kind: str | None = None) -> str | None:
    """Which version would play right now (for match attribution). With nothing
    saved, returns the synthetic `STARTER_VERSION` when the game ships a starter of
    that kind (its seeded default plays), else None."""
    kind = kind or harness_kind(game_id)
    side = _side(_entry_for(_read_all(), game_id), kind)
    active = side.get("active")
    versions = side.get("versions") or {}
    if active in versions and _is_authored(versions[active], kind):
        return str(active)
    return STARTER_VERSION if _has_starter(game_id, kind) else None


# ---- writing a harness --------------------------------------------------------


def _mutate(game_id: str, kind: str, fn: Callable[[dict[str, Any]], Any]) -> Any:
    """Run `fn` over one kind's `{active, versions}` block and persist the result.
    Every write goes through here, so the v1/v2 → v3 upgrade happens exactly once
    per game and only the touched side is rewritten."""
    data = _read_all()
    entry = _as_v3(data.get(game_id, {}), game_id)
    side = entry.setdefault(kind, {"active": "", "versions": {}})
    side.setdefault("versions", {})
    result = fn(side)
    data[game_id] = entry
    _write_all(data)
    return result


def save_harness(harness: Harness) -> Harness:
    """Overwrite the active version of this harness's kind in place (the PUT path)."""
    import time

    def apply(side: dict[str, Any]) -> None:
        versions = side["versions"]
        if not side.get("active") or side["active"] not in versions:
            side["active"] = side.get("active") or "v1"
        body = versions.setdefault(
            side["active"], {"label": side["active"], "created_at": time.time()}
        )
        body.update(harness.to_wire())
        body.pop("game_id", None)

    _mutate(harness.game_id, harness.kind, apply)
    return harness


def list_versions(game_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    """Version summaries for one harness kind, newest first. With nothing authored,
    a game that ships a starter of that kind lists one synthetic, active `starter`
    version (kept in sync with `active_version_id`)."""
    kind = kind or harness_kind(game_id)
    side = _side(_entry_for(_read_all(), game_id), kind)
    versions = {
        vid: body
        for vid, body in (side.get("versions") or {}).items()
        if _is_authored(body, kind)
    }
    if not versions:
        if _has_starter(game_id, kind):
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
    return sorted(
        (
            {
                "id": vid,
                "label": str(body.get("label") or vid),
                "created_at": float(body.get("created_at") or 0.0),
                "active": vid == side.get("active"),
                "model": body.get("model"),
            }
            for vid, body in versions.items()
        ),
        key=lambda v: v["created_at"],
        reverse=True,
    )


def save_version(game_id: str, harness: Harness, label: str = "") -> str:
    """Branch: save `harness` as a NEW version of its kind and make it active."""
    import time

    def apply(side: dict[str, Any]) -> str:
        versions = side["versions"]
        n = 1
        while f"v{n}" in versions:
            n += 1
        vid = f"v{n}"
        body = harness.to_wire()
        body.pop("game_id", None)
        body["label"] = label or vid
        body["created_at"] = time.time()
        versions[vid] = body
        side["active"] = vid
        return vid

    return str(_mutate(game_id, harness.kind, apply))


def activate_version(game_id: str, version_id: str, kind: str | None = None) -> bool:
    kind = kind or harness_kind(game_id)
    if game_id not in _read_all():
        return False

    def apply(side: dict[str, Any]) -> bool:
        if version_id not in (side.get("versions") or {}):
            return False
        side["active"] = version_id
        return True

    return bool(_mutate(game_id, kind, apply))


def delete_version(game_id: str, version_id: str, kind: str | None = None) -> bool:
    """Delete a version (never the last one; deleting the active one activates the
    newest remaining)."""
    kind = kind or harness_kind(game_id)
    if game_id not in _read_all():
        return False

    def apply(side: dict[str, Any]) -> bool:
        versions = side.get("versions") or {}
        if version_id not in versions or len(versions) <= 1:
            return False
        del versions[version_id]
        if side.get("active") == version_id:
            side["active"] = max(
                versions, key=lambda vid: float(versions[vid].get("created_at") or 0.0)
            )
        return True

    return bool(_mutate(game_id, kind, apply))


# ---- runtime ---------------------------------------------------------------


class HarnessRuntime:
    """Compiles a loadout's tools once and invokes them, capturing errors so a
    buggy tool degrades the agent's play (its own problem) rather than crashing the
    turn."""

    def __init__(self, loadout: LlmHarness) -> None:
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
