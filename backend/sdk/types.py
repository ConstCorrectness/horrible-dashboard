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
from typing import Any, Literal

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
    # When set, the tool is disclosed progressively under this group (loaded on
    # demand / by keyword) instead of always-present. `None` keeps the default:
    # the tool is part of the always-loaded core, unchanged from before.
    group: str | None = None

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


# --- connectors ------------------------------------------------------------
#
# A connector is one external account the node can hold credentials for (GitHub,
# Google, an API-key provider…). It owns the connect/disconnect flow and the
# credential; the agent tools it enables are ordinary `AgentTool`s registered
# alongside it. The home page renders whatever is registered here.

# How a connector is connected. `oauth` runs a provider handshake (device or
# redirect); `api-key` takes a pasted secret; `custom` is any other multi-step
# flow (Clubhouse's phone -> SMS code). All three drive the same begin/submit/poll
# machine — `custom` is just "a form step that may return another form step".
ConnectorKind = Literal["oauth", "api-key", "custom"]

# One step of a connect flow, returned by `begin`/`submit`/`poll`:
#   {"step": "device",   "user_code": ..., "verification_uri": ..., "interval": 5}
#   {"step": "redirect", "authorize_url": ...}
#   {"step": "form",     "fields": [{"name": "api_key", "secret": True}]}
#   {"connected": True,  "account": {...}}
#   {"pending": True} | {"error": "..."}
ConnectorStep = dict[str, Any]

ConnectorBegin = Callable[[dict[str, Any]], Awaitable[ConnectorStep]]
ConnectorSubmit = Callable[[dict[str, Any]], Awaitable[ConnectorStep]]
ConnectorPoll = Callable[[], Awaitable[ConnectorStep]]
ConnectorDisconnect = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class ConnectorScope:
    """One permission the connector asks the provider for, in the user's words.
    Rendered in the tile popover so "what did I grant this thing" is answerable
    without reading the source."""

    id: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class ConnectorAccount:
    """The connected identity, for display only — never a credential."""

    id: str
    label: str
    avatar_url: str | None = None


@dataclass(frozen=True)
class ConnectorStatus:
    """A connector's current state. `error` is for a connection that exists but is
    unusable (revoked token, unreadable credential) — distinct from `connected=False`,
    which means "never connected". Collapsing the two is what makes a broken
    integration look like an unconfigured one."""

    connected: bool
    account: ConnectorAccount | None = None
    scopes: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class Connector:
    """An external account the node can connect to and hold credentials for.

    `id` MUST equal the namespace of the agent tools it enables (`github` ->
    `github.searchCode`), because the orchestrator derives a tool's group from its
    name prefix (`_group_of`) — the `AgentTool.group` field does not name the group.
    Keeping them equal is what lets one connector definition feed both the home tile
    and the agent's tool-group catalog."""

    id: str
    label: str
    kind: ConnectorKind
    # Icon slug the frontend resolves against its icon map; unknown slugs fall back
    # to a letter avatar, so a third-party connector still renders.
    icon: str
    # One line. Doubles as the agent tool-group blurb in `list_tool_groups`.
    blurb: str
    status: Callable[[], ConnectorStatus]
    begin: ConnectorBegin
    disconnect: ConnectorDisconnect
    scopes: list[ConnectorScope] = field(default_factory=list)
    # SKILL.md-style guide injected into the agent's context when its tool group is
    # loaded. A callable is resolved lazily so a guide can be read from a file.
    guide: str | Callable[[], str] | None = None
    submit: ConnectorSubmit | None = None
    poll: ConnectorPoll | None = None
    # Whether this node has the client credentials the connector needs to start a flow
    # (an OAuth client id, sometimes a secret). Set it when the connector lets the user
    # supply those in the UI; leaving it None means "nothing to configure". It returns a
    # bool, never the credential — the whole point is that the secret stays server-side.
    configured: Callable[[], bool] | None = None

    def resolve_guide(self) -> str | None:
        """The guide text, calling the factory if one was given."""
        if callable(self.guide):
            return self.guide()
        return self.guide
