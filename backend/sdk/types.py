"""Public types for the backend plugin SDK.

A third-party (or first-party) backend plugin contributes server-side capabilities
— HTTP routes, agent tools, `/ws` channels, lifespan hooks, and `dash` facades —
the same way a built-in module does. v1 is **trusted and unsandboxed** (plugin code
runs in-process with full backend access); it is meant for localhost use, mirroring
the frontend SDK's v1 trust model. See docs/architecture/python-sdk.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# A backend agent-tool handler: receives the model's arguments, returns a JSON-able
# result. May be sync or async — the orchestrator awaits coroutines.
AgentToolHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]

# A `/ws` channel handler: receives the connection and the raw frame; replies via
# `conn.send_json(...)`. Always async.
WsChannelHandler = Callable[[Any, dict[str, Any]], Awaitable[None]]

# A lifespan hook (startup or shutdown). May be sync or async.
LifecycleHook = Callable[[], Any | Awaitable[Any]]

# Builds the object bound onto `dash.<name>` in each REPL session.
DashFacadeFactory = Callable[[], Any]


@dataclass(frozen=True)
class PluginManifest:
    """Identity of a backend plugin."""

    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""


@dataclass
class AgentTool:
    """A server-side agent tool. Unlike browser tools, its handler runs in the
    backend, so it works with no tab attached. `side_effect=True` routes it through
    the agent permission gate (read-only tools pass straight through)."""

    name: str
    description: str
    handler: AgentToolHandler
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    side_effect: bool = False
    specifier_template: str | None = None

    def provider_tool(self) -> dict[str, Any]:
        """The provider (Ollama/OpenAI) tool definition for this tool."""
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

    def meta(self) -> dict[str, Any]:
        """Permission-gate metadata (matches the shape the orchestrator expects)."""
        return {
            "name": self.name,
            "sideEffect": self.side_effect,
            "specifierTemplate": self.specifier_template,
        }
