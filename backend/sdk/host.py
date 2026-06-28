"""The `PluginHost` a plugin's `setup()` receives, plus the `BackendPlugin` base.

A plugin module exposes a `PLUGIN` object (a `BackendPlugin`). At load time the
loader calls `plugin.setup(host)`; the plugin uses `host` to register its
capabilities, which land in the process-global [registry](registry.py).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from fastapi import APIRouter

from backend.sdk.registry import PluginRegistry, _MountedRouter, registry
from backend.sdk.types import (
    AgentTool,
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

    def add_router(self, router: APIRouter, *, prefix: str | None = None) -> None:
        """Mount a FastAPI router. Default prefix is `/api/plugins/<id>`; pass an
        explicit `prefix` (joined under `/api`) to override."""
        resolved = prefix if prefix is not None else f"/plugins/{self._manifest.id}"
        self._registry.routers.append(
            _MountedRouter(router=router, prefix=resolved, plugin_id=self._manifest.id)
        )

    def add_agent_tool(self, tool: AgentTool) -> None:
        """Expose a server-side agent tool. Backend plugins win name conflicts in load
        order; a name clash with a core tool is the plugin author's responsibility."""
        self._registry.agent_tools[tool.name] = tool

    def add_ws_channel(self, channel: str, handler: WsChannelHandler) -> None:
        """Handle a new channel on the shared `/ws` socket. Built-in channels
        (`agent`, `collab`, …) take precedence; pick a unique channel name."""
        self._registry.ws_channels[channel] = handler

    def add_dash_facade(self, name: str, factory: DashFacadeFactory) -> None:
        """Attach `dash.<name>` in every REPL session (factory builds the object)."""
        self._registry.dash_facades[name] = factory

    def on_startup(self, hook: LifecycleHook) -> None:
        """Run `hook` when the app starts (inside the lifespan). Sync or async."""
        self._registry.startup_hooks.append(hook)

    def on_shutdown(self, hook: LifecycleHook) -> None:
        """Run `hook` when the app shuts down. Sync or async."""
        self._registry.shutdown_hooks.append(hook)


class BackendPlugin(ABC):
    """Base class for a backend plugin. Subclass it, set `manifest`, and implement
    `setup` to register capabilities. A plugin module must expose its instance as a
    module-level `PLUGIN`."""

    manifest: PluginManifest

    @abstractmethod
    def setup(self, host: PluginHost) -> None:
        """Register this plugin's capabilities via `host`."""
        raise NotImplementedError
