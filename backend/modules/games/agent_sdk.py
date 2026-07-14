"""The **agent** a player builds — the code-first entrypoint and the SDK it calls.

A loadout's `agent_code` (optional) defines `my_agent(obs, config)` returning the id of
a legal action. This is the top authoring layer over the declarative harness
(`context` + `tools` + `model`, see [loadout.py](loadout.py)):

- **Empty `agent_code` ⇒ the default agent** — the declarative harness *is* the agent:
  the model drives the player's tools and commits a move (today's `AgentPolicy` loop).
  Nothing changes for players who never write agent code.
- **Custom `agent_code` ⇒ your code runs.** From a one-liner
  (`return random.choice(obs["legal_actions"])["id"]`) up to a fully-harnessed agent doing
  RAG / memory / context engineering — the depth is up to the game and the player. The
  provided SDK is the deep end:

      async def my_agent(obs, config):
          docs = await config.retrieve(obs.get("query", ""))   # your library (RAG)
          config.memory.store({"turn": obs.get("tick"), "docs": len(docs)})
          config.note(f"grounded on {len(docs)} docs")
          return await config.decide(obs)      # run the context+tools model loop

`my_agent` may be sync or async; the policy awaits a coroutine result. Its return value is
coerced to a legal action id (a bare id, an int, or an action dict — the shape a game's
`legal_actions` carries). Returning nothing / an illegal id falls back to a random legal
move in a live match, and surfaces as "no move" in the dry-run tester.

Trust model: `agent_code` is the author's own Python on the author's own node, compiled
like a tool (`_compile_tool`) — trusted and unsandboxed in v1, **not** gated by
`GAMES_ENABLE_CODE_EXEC` (that gate is for grading *others'* submitted code). See
docs/modules/games.mdx.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Episodic memory keyed by game — persists across the moves of a match within this
# process. A first, deliberately small store; a durable/vector-backed memory is a
# later phase. Reset via `config.memory.clear()`.
_MEMORY: dict[str, list[Any]] = {}


class MatchMemory:
    """Per-game episodic scratchpad the agent can write to across turns of a match."""

    def __init__(self, game_id: str) -> None:
        self._game = game_id or "default"

    def store(self, item: Any) -> None:
        _MEMORY.setdefault(self._game, []).append(item)

    def recall(self, obs: Any = None, k: int = 8) -> list[Any]:
        """The most recent `k` remembered items (obs is accepted for symmetry with
        richer future recall, e.g. similarity search, and currently unused)."""
        return _MEMORY.get(self._game, [])[-max(0, int(k)) :]

    def clear(self) -> None:
        _MEMORY.pop(self._game, None)


async def retrieve(
    query: str, source: str = "default", k: int = 5
) -> list[dict[str, Any]]:
    """RAG: semantic-search the app's vector store (the `library` collection `source`)
    for `query`, returning `[{text, score}]`. Best-effort — an unconfigured embedder or
    empty store yields `[]` rather than failing the move."""
    try:
        from backend.modules.database.embeddings import get_embedding
        from backend.modules.database.vectorstore import search_documents

        embedding, _src = await get_embedding(str(query))
        rows = search_documents(source or "default", embedding, int(k))
        return [
            {"text": r.get("text", ""), "score": float(r.get("score", 0.0))}
            for r in rows
        ]
    except Exception:  # noqa: BLE001 — retrieval is optional; never break the turn
        logger.debug("agent retrieve() failed", exc_info=True)
        return []


class AgentConfig:
    """The `config` handed to `my_agent(obs, config)`: a read-only view of the loadout
    plus the SDK. `decide(obs)` runs the declarative context+tools+model loop (the
    default agent), so custom code can lean on it (`return await config.decide(obs)`) or
    ignore it entirely."""

    def __init__(
        self,
        *,
        loadout: Any,
        legal_actions: list[dict[str, Any]],
        ids: list[str],
        run_declarative: Callable[[dict[str, Any]], Awaitable[str | None]],
        emit: Callable[..., None] | None = None,
    ) -> None:
        self._loadout = loadout
        self.legal_actions = legal_actions
        self.ids = ids
        self._run_declarative = run_declarative
        self._emit = emit or (lambda *a, **k: None)
        self.memory = MatchMemory(getattr(loadout, "game_id", "default"))

    # read-only harness view
    @property
    def context(self) -> str:
        return getattr(self._loadout, "context", "") or ""

    @property
    def tools(self) -> list[str]:
        return [t.name for t in getattr(self._loadout, "tools", [])]

    @property
    def model(self) -> dict[str, Any] | None:
        return getattr(self._loadout, "model", None)

    @property
    def game_id(self) -> str:
        return getattr(self._loadout, "game_id", "default")

    # SDK
    def note(self, text: Any) -> None:
        """Surface a line in the live agent feed / dry-run trace."""
        self._emit("assistant", content=str(text), tool_calls=[])

    async def retrieve(
        self, query: str, source: str = "default", k: int = 5
    ) -> list[dict[str, Any]]:
        return await retrieve(query, source, k)

    async def decide(self, obs: dict[str, Any]) -> str | None:
        """Run the default agent — the model driving your context + tools — and return
        the committed legal action id (or None if it never committed)."""
        return await self._run_declarative(obs)

    def harness(self) -> "Harness":
        return Harness(self)


class Harness:
    """Ergonomic wrapper over `AgentConfig` for the `Harness(config).decide(obs)` idiom.
    Every method is chainable; the real work is `decide`, which runs the context+tools
    model loop."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def equip(self, tool: Any) -> "Harness":  # tools already come from the loadout
        return self

    def remember(self, item: Any) -> "Harness":
        self.config.memory.store(item)
        return self

    def ground(self, docs: Any) -> "Harness":
        self.config.note(
            f"grounded on {len(docs) if hasattr(docs, '__len__') else '?'} docs"
        )
        return self

    def note(self, text: Any) -> "Harness":
        self.config.note(text)
        return self

    async def decide(self, obs: dict[str, Any]) -> str | None:
        return await self.config.decide(obs)


def compile_agent(code: str) -> Callable[..., Any]:
    """Exec `agent_code` and return its `my_agent` callable. `Harness` is in scope so
    `Harness(config)` works without an import. Trusted (author's own node), like
    `_compile_tool`."""
    namespace: dict[str, Any] = {"Harness": Harness}
    exec(compile(code, "<agent>", "exec"), namespace)  # noqa: S102 (trusted own-node code)
    fn = namespace.get("my_agent")
    if not callable(fn):
        raise ValueError("agent code must define a callable `my_agent(obs, config)`")
    return fn


def agent_compile_error(code: str) -> str | None:
    """Why `agent_code` won't run, or None if it's fine (for the editor's diagnostics).
    Empty code is valid — it means 'use the default agent'."""
    if not (code or "").strip():
        return None
    try:
        compile_agent(code)
        return None
    except Exception as exc:  # noqa: BLE001 — surfaced to the author
        return f"{type(exc).__name__}: {exc}"


def coerce_action_id(result: Any, ids: list[str]) -> str | None:
    """Normalize `my_agent`'s return value to a legal action id, or None. Accepts a bare
    id, an int, or an action dict (`{"id": ...}` / `action_id` / `action`) — the shape a
    game's `legal_actions` carries."""
    if result is None:
        return None
    if isinstance(result, dict):
        for key in ("id", "action_id", "action"):
            if key in result:
                return coerce_action_id(result[key], ids)
        return None
    if isinstance(result, (list, tuple, set)):
        return None  # ambiguous — the agent must pick one
    candidate = str(result)
    return candidate if candidate in ids else None


# The canonical default agent, shown in the editor when seeding a new agent. It exactly
# reproduces the built-in behavior — the model driving your context + tools — and is a
# starting point to override. (When `agent_code` is left empty the policy runs this
# behavior directly, without exec'ing this text.)
DEFAULT_AGENT_SOURCE = '''async def my_agent(obs, config):
    """Your agent. The default lets your context + tools drive the model — but this is
    just Python: rewrite it to build any agent you like (RAG, memory, search, ...)."""
    return await config.decide(obs)
'''

# A RAG-flavored starter for retrieval games: ground the model on your library first.
RAG_AGENT_SOURCE = '''async def my_agent(obs, config):
    """Retrieval agent: pull from your library, then let the model decide."""
    docs = await config.retrieve(obs.get("query", ""), k=5)   # your library (RAG)
    config.note(f"retrieved {len(docs)} docs")
    return await config.decide(obs)
'''

# A trivial, model-free starter: the low end of the spectrum.
REFLEX_AGENT_SOURCE = '''def my_agent(obs, config):
    """A one-liner agent: pick a legal action. Build up from here."""
    import random
    return random.choice(obs["legal_actions"])["id"]
'''

# Which starter seeds the editor for a new agent, per game. Everything defaults to the
# canonical default (context + tools drive the model); retrieval games start RAG-shaped.
# The depth of the starter is the only thing that varies per game — the editor is the
# same everywhere (see docs/modules/games.mdx, "build your agent").
_STARTERS: dict[str, str] = {
    "rag_race": RAG_AGENT_SOURCE,
}


def starter_agent_source(game_id: str) -> str:
    """The `agent_code` to pre-fill the editor with for a fresh agent on `game_id`."""
    return _STARTERS.get(game_id, DEFAULT_AGENT_SOURCE)
