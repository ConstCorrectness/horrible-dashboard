"""The `PluginHost` a plugin's `setup()` receives, plus the `BackendPlugin` base.

A plugin module exposes a `PLUGIN` object (a `BackendPlugin`). At load time the
loader calls `plugin.setup(host)`; the plugin uses `host` to register its
capabilities, which land in the process-global [registry](registry.py).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from fastapi import APIRouter

from backend.sdk.registry import PluginRegistry, _MountedRouter, registry
from backend.sdk.types import (
    AgentTool,
    Connector,
    DashFacadeFactory,
    LifecycleHook,
    PluginManifest,
    WsChannelHandler,
)


class PluginHost:
    """The capability-registration surface handed to a plugin's `setup()`. Every
    method namespaces the contribution under the plugin's id so the app can attribute
    it. A plugin should call these only from `setup()`."""

    def __init__(
        self, manifest: PluginManifest, reg: PluginRegistry | None = None
    ) -> None:
        self._manifest = manifest
        self._registry = reg if reg is not None else registry
        self.log = logging.getLogger(f"plugin.{manifest.id}")

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    @property
    def owner_id(self) -> str:
        """The id every contribution is recorded under, for unloading."""
        return self._manifest.id

    def _own(self, kind: str, key: object) -> None:
        self._registry.own(self.owner_id, kind, key)

    def add_router(self, router: APIRouter, *, prefix: str | None = None) -> None:
        """Mount a FastAPI router. Default prefix is `/api/plugins/<id>`; pass an
        explicit `prefix` (joined under `/api`) to override."""
        resolved = prefix if prefix is not None else f"/plugins/{self._manifest.id}"
        mounted = _MountedRouter(router=router, prefix=resolved, plugin_id=self._manifest.id)
        self._registry.routers.append(mounted)
        self._own("routers", mounted)

    def add_agent_tool(self, tool: AgentTool) -> None:
        """Expose a server-side agent tool. Backend plugins win name conflicts in load
        order; a name clash with a core tool is the plugin author's responsibility."""
        self._registry.agent_tools[tool.name] = tool
        self._own("agent_tools", tool.name)

    def add_agent(self, spec: AgentSpec) -> None:
        """Register a specialized agent (own system prompt, tool-group scope, and
        per-agent `agent.<id>.*` settings). It appears in the roster next to the
        built-ins and is addressable from the chat widget and `agent.delegate`.
        Built-in agent ids (main, coder, dba, researcher) cannot be overridden."""
        self._registry.agents[spec.id] = spec
        self._own("agents", spec.id)

    def add_connector(self, connector: Connector) -> None:
        """Expose an external account the user can connect from the home page, and
        whose credential the node holds server-side.

        Name the connector's agent tools `<connector.id>.*` — the orchestrator groups
        tools by their name prefix, so a mismatch silently splits the connector's
        tools off from its blurb and guide."""
        self._registry.connectors[connector.id] = connector
        self._own("connectors", connector.id)

    def add_ws_channel(self, channel: str, handler: WsChannelHandler) -> None:
        """Handle a new channel on the shared `/ws` socket. Built-in channels
        (`agent`, `collab`, …) take precedence; pick a unique channel name."""
        self._registry.ws_channels[channel] = handler
        self._own("ws_channels", channel)

    def add_training_provider(self, provider: Any) -> None:
        """Expose a training environment provider (an object satisfying
        `backend.modules.training.providers.base.EnvironmentProvider`): search,
        resolve, fetch, and scaffold one kind of trainable environment. Built-in
        providers (kaggle, huggingface, gymnasium) win id conflicts."""
        self._registry.training_providers[provider.provider] = provider
        self._own("training_providers", provider.provider)

    def add_dataset_source(self, source: Any) -> None:
        """Expose a place training material lives (an object satisfying
        `backend.modules.datasets.sources.DatasetSource`): search, splits, peek and
        locate for one source. It appears in the datasets browser alongside the Hub
        and the local files, and a recipe can point at it like any other.

        Built-in sources (hub, local, exports, kaggle) win id conflicts."""
        self._registry.dataset_sources[source.id] = source
        self._own("dataset_sources", source.id)

    def add_recipe_backend(self, backend: Any) -> None:
        """Expose a training framework (an object satisfying
        `backend.modules.training.backends.base.RecipeBackend`): which tasks it can
        train, which knobs it takes, and the code it emits for them.

        The field catalog it returns is validated against the project venv by the
        same introspection every built-in backend goes through, so a knob the
        installed library does not accept is dropped from the emitted code and shown
        as dropped rather than emitted hopefully. Built-ins (trl, unsloth,
        torchtitan, nanotron) win id conflicts."""
        self._registry.recipe_backends[backend.id] = backend
        self._own("recipe_backends", backend.id)

    def add_eval_runner(self, runner: Any) -> None:
        """Expose a way of executing an eval suite (an object satisfying
        `backend.modules.evals.runners.EvalRunner`): which case types it claims and
        how it runs them. Results land in the same tables as every other runner's,
        so a mixed suite still adds up. Built-ins win id conflicts."""
        self._registry.eval_runners[runner.id] = runner
        self._own("eval_runners", runner.id)

    def add_search_provider(self, provider: Any) -> None:
        """Expose a web-search provider (an object satisfying
        `backend.modules.search.base.SearchProvider`): one `search(query, ...)` that
        returns ranked `SearchResult`s. It joins the fan-out and rank fusion
        alongside the built-ins, so a plugin's provider is indistinguishable from
        Tavily or the node's own crawl index. Reusing a built-in's id overrides it."""
        from backend.modules.search.base import register_provider

        self._registry.search_providers[provider.id] = provider
        register_provider(provider)
        self._own("search_providers", provider.id)

    def add_music_source(self, source: Any) -> None:
        """Expose a music source (satisfying
        `backend.modules.music.providers.MusicSource`): `search()` returns
        candidates, `fetch()` downloads one into the library. The agent's
        `music.play` and the import resolver use it with no new tools. Reusing a
        built-in's id (`youtube`) replaces it."""
        from backend.modules.music.providers import sources

        self._registry.music_sources[source.id] = source
        sources.register(source)
        self._own("music_sources", source.id)

    def add_playlist_importer(self, importer: Any) -> None:
        """Expose a playlist importer (`backend.modules.music.providers.
        PlaylistImporter`): an account that lists playlists, or a file format it
        parses. Imported tracks are resolved and downloaded by the music library."""
        from backend.modules.music.providers import importers

        self._registry.playlist_importers[importer.id] = importer
        importers.register(importer)
        self._own("playlist_importers", importer.id)

    def add_music_listener(self, listener: Any) -> None:
        """Expose a listener (`backend.modules.music.providers.MusicListener`): told
        what played (scrobbling) and asked what's similar (the DJ and radio)."""
        from backend.modules.music.providers import listeners

        self._registry.music_listeners[listener.id] = listener
        listeners.register(listener)
        self._own("music_listeners", listener.id)

    def add_dash_facade(self, name: str, factory: DashFacadeFactory) -> None:
        """Attach `dash.<name>` in every REPL session (factory builds the object)."""
        self._registry.dash_facades[name] = factory
        self._own("dash_facades", name)

    def on_startup(self, hook: LifecycleHook) -> None:
        """Run `hook` when the app starts (inside the lifespan). Sync or async."""
        self._registry.startup_hooks.append(hook)
        self._own("startup_hooks", hook)

    def on_shutdown(self, hook: LifecycleHook) -> None:
        """Run `hook` when the app shuts down. Sync or async."""
        self._registry.shutdown_hooks.append(hook)
        self._own("shutdown_hooks", hook)


class BackendPlugin(ABC):
    """Base class for a backend plugin. Subclass it, set `manifest`, and implement
    `setup` to register capabilities. A plugin module must expose its instance as a
    module-level `PLUGIN`."""

    manifest: PluginManifest

    @abstractmethod
    def setup(self, host: PluginHost) -> None:
        """Register this plugin's capabilities via `host`."""
        raise NotImplementedError
