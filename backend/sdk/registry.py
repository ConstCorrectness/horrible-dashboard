"""The process-global backend-plugin registry.

Plugins write into this via their `PluginHost` at load time; the app, the agent
orchestrator, the `/ws` loop, and the REPL read from it. Kept dependency-light (no
app/orchestrator imports) so any of those can read it without a cycle.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter

from backend.sdk.types import (
    AgentTool,
    Connector,
    DashFacadeFactory,
    LifecycleHook,
    PluginManifest,
    WsChannelHandler,
    WsDisconnectHook,
)

logger = logging.getLogger(__name__)


@dataclass
class _MountedRouter:
    router: APIRouter
    prefix: str
    plugin_id: str


@dataclass
class PluginRegistry:
    """Everything backend plugins have contributed, aggregated across all plugins."""

    loaded: list[PluginManifest] = field(default_factory=list)
    routers: list[_MountedRouter] = field(default_factory=list)
    agent_tools: dict[str, AgentTool] = field(default_factory=dict)
    # Plugin-contributed specialized agents, keyed by agent id. Built-in agents
    # (main/coder/dba/researcher) live in the agent module's roster and win id
    # conflicts.
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    # External accounts the node can connect to, keyed by connector id. The id also
    # names the agent tool group the connector enables (see Connector.id).
    connectors: dict[str, Connector] = field(default_factory=dict)
    ws_channels: dict[str, WsChannelHandler] = field(default_factory=dict)
    dash_facades: dict[str, DashFacadeFactory] = field(default_factory=dict)
    # Environment providers for the training module (duck-typed against
    # backend.modules.training.providers.base.EnvironmentProvider; kept as Any so
    # the SDK stays dependency-light).
    training_providers: dict[str, Any] = field(default_factory=dict)
    # Web-search providers (duck-typed against
    # backend.modules.search.base.SearchProvider). Built-ins register themselves into
    # that module's own registry; this mirror is what `host.add_search_provider`
    # writes to so a plugin's provider is discoverable the same way.
    search_providers: dict[str, Any] = field(default_factory=dict)
    # Music library providers (duck-typed against backend.modules.music.providers):
    # sources fetch into the library, importers describe playlists, listeners
    # scrobble and answer "similar". Mirrors of the music module's own registries.
    music_sources: dict[str, Any] = field(default_factory=dict)
    playlist_importers: dict[str, Any] = field(default_factory=dict)
    music_listeners: dict[str, Any] = field(default_factory=dict)
    # Dataset sources (duck-typed against
    # backend.modules.datasets.sources.DatasetSource): one place training material
    # lives, searchable and peekable. Built-ins win id conflicts.
    dataset_sources: dict[str, Any] = field(default_factory=dict)
    # Recipe backends (duck-typed against
    # backend.modules.training.backends.base.RecipeBackend): one training framework,
    # its tasks, its fields and the code it emits. Built-ins win id conflicts.
    recipe_backends: dict[str, Any] = field(default_factory=dict)
    # Eval runners (duck-typed against backend.modules.evals.runners.EvalRunner):
    # one way of executing a suite. Built-ins win id conflicts.
    eval_runners: dict[str, Any] = field(default_factory=dict)
    startup_hooks: list[LifecycleHook] = field(default_factory=list)
    shutdown_hooks: list[LifecycleHook] = field(default_factory=list)
    # Called with the connection when a `/ws` socket closes (module cleanup that is
    # per-connection, e.g. leaving a match).
    ws_disconnect_hooks: list[WsDisconnectHook] = field(default_factory=list)
    # Typed cross-module services: contract id ("audio.mixer@1") → (owner, impl).
    # The Python twin of the frontend `ServiceContracts` seam.
    services: dict[str, tuple[str, Any]] = field(default_factory=dict)
    # Per-plugin load failures, surfaced rather than crashing the app.
    errors: list[tuple[str, str]] = field(default_factory=list)
    # Everything each owner (a plugin or module id) contributed, as (kind, key)
    # pairs, so `unregister_owner` can take exactly that back out. Kinds name the
    # registry dict (`agent_tools`, …) or a hook list (`startup_hooks`, …).
    owned: dict[str, list[tuple[str, Any]]] = field(default_factory=dict)

    # --- ownership ------------------------------------------------------------

    def own(self, owner: str, kind: str, key: Any) -> None:
        """Record that `owner` contributed `key` to `kind`."""
        self.owned.setdefault(owner, []).append((kind, key))

    def owner_of_tool(self, name: str) -> str | None:
        for owner, items in self.owned.items():
            if ("agent_tools", name) in items:
                return owner
        return None

    def unregister_owner(self, owner: str) -> list[tuple[str, Any]]:
        """Remove everything `owner` contributed. Idempotent; returns what was removed.

        A key another owner has since replaced is left alone — the later registration
        won, and unloading the earlier one must not take it out."""
        removed: list[tuple[str, Any]] = []
        for kind, key in reversed(self.owned.pop(owner, [])):
            target = getattr(self, kind, None)
            if isinstance(target, dict):
                if key in target and self._still_owned_by(owner, kind, key):
                    target.pop(key, None)
                    removed.append((kind, key))
            elif isinstance(target, list):
                if key in target:
                    target.remove(key)
                    removed.append((kind, key))
            unhook = _SIDE_UNREGISTER.get(kind)
            if unhook is not None:
                try:
                    unhook(key)
                except Exception:  # noqa: BLE001 — unloading must finish
                    logger.exception("unregistering %s %s for %s failed", kind, key, owner)
        self.loaded = [m for m in self.loaded if m.id != owner]
        return removed

    def _still_owned_by(self, owner: str, kind: str, key: Any) -> bool:
        """No *other* owner registered the same key after `owner` did."""
        return not any(
            (kind, key) in items for other, items in self.owned.items() if other != owner
        )

    def provide(self, owner: str, contract: str, impl: Any) -> None:
        self.services[contract] = (owner, impl)
        self.own(owner, "services", contract)

    def service(self, contract: str) -> Any | None:
        entry = self.services.get(contract)
        return entry[1] if entry else None

    def reset(self) -> None:
        """Drop all registrations (used between test loads)."""
        self.loaded.clear()
        self.routers.clear()
        self.agent_tools.clear()
        self.agents.clear()
        self.connectors.clear()
        self.ws_channels.clear()
        self.dash_facades.clear()
        self.training_providers.clear()
        self.search_providers.clear()
        self.music_sources.clear()
        self.playlist_importers.clear()
        self.music_listeners.clear()
        self.dataset_sources.clear()
        self.recipe_backends.clear()
        self.eval_runners.clear()
        self.startup_hooks.clear()
        self.shutdown_hooks.clear()
        self.ws_disconnect_hooks.clear()
        self.services.clear()
        self.errors.clear()
        self.owned.clear()

    # --- reads used by the app / orchestrator / repl -----------------------

    def provider_tools(self, *, grouped: bool | None = None) -> list[dict[str, Any]]:
        """Plugin agent tools as provider tool definitions (for the model).

        `grouped=None` returns all (back-compat). `grouped=False` returns only
        always-core tools (no `group`); `grouped=True` returns only the ones
        disclosed under a group, for the progressive-disclosure pool."""
        return [
            t.provider_tool()
            for t in self.agent_tools.values()
            if grouped is None
            or (grouped is False and t.group is None)
            or (grouped is True and t.group is not None)
        ]

    async def invoke_agent_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Run a plugin agent tool by name, awaiting an async handler. Failures come
        back as an `{'error': ...}` value so one bad tool can't break a turn."""
        tool = self.agent_tools.get(name)
        if tool is None:
            return {"error": f"unknown plugin tool {name}"}
        try:
            result = tool.handler(args)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:  # noqa: BLE001 — tool errors are values, not crashes
            logger.exception("plugin agent tool %s failed", name)
            return {"error": f"{type(exc).__name__}: {exc}"}

    async def dispatch_ws(
        self, conn: Any, channel: str, message: dict[str, Any]
    ) -> bool:
        """Route a `/ws` frame to a plugin channel handler. Returns True if handled."""
        handler = self.ws_channels.get(channel)
        if handler is None:
            return False
        try:
            await handler(conn, message)
        except Exception:  # noqa: BLE001 — never break the receive loop
            logger.exception("plugin ws channel %s handler failed", channel)
        return True

    def _hooks_of(self, kind: str, owner: str | None) -> list[Any]:
        hooks: list[Any] = getattr(self, kind)
        if owner is None:
            return list(hooks)
        mine = [key for k, key in self.owned.get(owner, []) if k == kind]
        return [h for h in hooks if any(h is m for m in mine)]

    async def run_startup(self, owner: str | None = None) -> None:
        """Run startup hooks — all of them at app start, or one owner's when it is
        loaded into an app that is already running."""
        await _run_hooks(self._hooks_of("startup_hooks", owner), "startup")

    async def run_shutdown(self, owner: str | None = None) -> None:
        await _run_hooks(self._hooks_of("shutdown_hooks", owner), "shutdown")

    async def run_ws_disconnect(self, conn: Any) -> None:
        """Tell every module a `/ws` connection closed. Never raises."""
        for hook in list(self.ws_disconnect_hooks):
            try:
                result = hook(conn)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 — never break the socket teardown
                logger.exception("ws disconnect hook failed")


async def _run_hooks(hooks: list[LifecycleHook], phase: str) -> None:
    for hook in hooks:
        try:
            result = hook()
            if isinstance(result, Awaitable):
                await result
        except Exception:  # noqa: BLE001 — a bad hook shouldn't take the app down
            logger.exception("plugin %s hook failed", phase)


def _unregister_search(provider_id: str) -> None:
    from backend.modules.search.base import unregister_provider

    unregister_provider(provider_id)


def _music_unregister(attr: str) -> Callable[[str], None]:
    def unhook(provider_id: str) -> None:
        from backend.modules.music import providers

        getattr(providers, attr).unregister(provider_id)

    return unhook


# Registries that live in another module as well as here (the host writes both), so
# taking a contribution back out has to reach that module's copy too.
_SIDE_UNREGISTER: dict[str, Callable[[Any], None]] = {
    "search_providers": _unregister_search,
    "music_sources": _music_unregister("sources"),
    "playlist_importers": _music_unregister("importers"),
    "music_listeners": _music_unregister("listeners"),
}


# The one registry the whole process shares.
registry = PluginRegistry()
