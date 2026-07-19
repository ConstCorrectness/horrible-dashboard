"""The process-global backend-plugin registry.

Plugins write into this via their `PluginHost` at load time; the app, the agent
orchestrator, the `/ws` loop, and the REPL read from it. Kept dependency-light (no
app/orchestrator imports) so any of those can read it without a cycle.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable
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
    startup_hooks: list[LifecycleHook] = field(default_factory=list)
    shutdown_hooks: list[LifecycleHook] = field(default_factory=list)
    # Per-plugin load failures, surfaced rather than crashing the app.
    errors: list[tuple[str, str]] = field(default_factory=list)

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
        self.startup_hooks.clear()
        self.shutdown_hooks.clear()
        self.errors.clear()

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

    async def run_startup(self) -> None:
        await _run_hooks(self.startup_hooks, "startup")

    async def run_shutdown(self) -> None:
        await _run_hooks(self.shutdown_hooks, "shutdown")


async def _run_hooks(hooks: list[LifecycleHook], phase: str) -> None:
    for hook in hooks:
        try:
            result = hook()
            if isinstance(result, Awaitable):
                await result
        except Exception:  # noqa: BLE001 — a bad hook shouldn't take the app down
            logger.exception("plugin %s hook failed", phase)


# The one registry the whole process shares.
registry = PluginRegistry()
