"""Supervised MCP client sessions — one per configured server.

**Why each session lives in its own task.** The MCP SDK exposes transports and
`ClientSession` as async context managers, and anyio requires a context manager to be
exited by the same task that entered it. A long-lived session therefore cannot be opened
inside one request and used by the next. So each server gets a dedicated supervisor task
that enters the stack, initializes, discovers what the server offers, and then parks on a
stop event — keeping the session open and callable from any other task for as long as the
server is connected. Tearing it down is a matter of setting that event.

Discovery is deliberately eager and cached: a turn that loads an MCP tool group must not
pay a round-trip to enumerate tools while the model waits. Tools, prompts and resources
are read once at connect time and refreshed on the server's `listChanged` notifications
or an explicit reconnect.

A server that fails to start is an ordinary status (`error` + a message), not an
exception — a broken MCP server must not prevent the backend from booting, and the user
needs to see *why* it's broken in the servers pane. This mirrors how the plugin loader
surfaces per-plugin failures instead of crashing the app.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp import ClientSession
from mcp import types as mcp_types

from backend.modules.mcp import config as cfg
from backend.modules.mcp.transport import describe_target, popen_stdio_client

logger = logging.getLogger(__name__)

# How long a server gets to start up and finish the initialize handshake. Generous,
# because an `npx` server may be downloading its package on first run.
CONNECT_TIMEOUT_S = 90.0

# Per-tool-call ceiling. A hung MCP server must not wedge an agent turn forever.
CALL_TIMEOUT_S = 120.0

State = Literal["stopped", "starting", "ready", "error"]


@dataclass
class ToolInfo:
    """One tool a server exposes, flattened to what the bridge and UI need."""

    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False
    destructive: bool = False


@dataclass
class PromptInfo:
    name: str
    description: str


@dataclass
class ResourceInfo:
    uri: str
    name: str
    description: str
    mime_type: str | None = None


@dataclass
class ServerRuntime:
    """Live state for one configured server."""

    config: dict[str, Any]
    state: State = "stopped"
    error: str | None = None
    server_name: str = ""
    server_version: str = ""
    tools: list[ToolInfo] = field(default_factory=list)
    prompts: list[PromptInfo] = field(default_factory=list)
    resources: list[ResourceInfo] = field(default_factory=list)
    # Server-supplied usage docs, assembled from its `prompts` (see bridge.guide_for).
    instructions: str = ""

    @property
    def id(self) -> str:
        return str(self.config.get("id", ""))

    @property
    def group(self) -> str:
        return cfg.group_name(self.id)

    def public(self) -> dict[str, Any]:
        """The browser-safe view: config without secrets, plus live status."""
        conf = {k: v for k, v in self.config.items() if k != "headers"}
        target = (
            describe_target(
                str(self.config.get("command", "")),
                list(self.config.get("args", []) or []),
            )
            if self.config.get("transport") == "stdio"
            else {"url": self.config.get("url", ""), "available": True}
        )
        return {
            **conf,
            "group": self.group,
            "state": self.state,
            "error": self.error,
            "serverName": self.server_name,
            "serverVersion": self.server_version,
            "hasToken": cfg.has_auth_token(self.id),
            "target": target,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "readOnly": t.read_only,
                    "destructive": t.destructive,
                }
                for t in self.tools
            ],
            "prompts": [
                {"name": p.name, "description": p.description} for p in self.prompts
            ],
            "resources": [
                {"uri": r.uri, "name": r.name, "description": r.description}
                for r in self.resources
            ],
        }


class McpSession:
    """One supervised connection to an MCP server."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.runtime = ServerRuntime(config=config)
        self._session: ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()

    @property
    def id(self) -> str:
        return self.runtime.id

    async def start(self) -> None:
        """Launch the supervisor and wait until the server is ready or has failed."""
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self.runtime.state = "starting"
        self.runtime.error = None
        self._task = asyncio.create_task(self._run(), name=f"mcp-session-{self.id}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=CONNECT_TIMEOUT_S)
        except TimeoutError:
            self.runtime.state = "error"
            self.runtime.error = f"timed out after {CONNECT_TIMEOUT_S:.0f}s"
            await self.stop()

    async def stop(self) -> None:
        """Signal the supervisor to tear the session down and wait for it."""
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=15.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        self._session = None
        if self.runtime.state != "error":
            self.runtime.state = "stopped"

    async def _open(self, stack: AsyncExitStack) -> ClientSession:
        """Enter the right transport for this config and return an open session."""
        conf = self.runtime.config
        transport = conf.get("transport")
        if transport == "stdio":
            read, write = await stack.enter_async_context(
                popen_stdio_client(
                    str(conf.get("command", "")),
                    [str(a) for a in conf.get("args", []) or []],
                    {str(k): str(v) for k, v in (conf.get("env") or {}).items()},
                    conf.get("cwd") or None,
                )
            )
        else:
            headers = {str(k): str(v) for k, v in (conf.get("headers") or {}).items()}
            # The token never lives in the plaintext config — it's fetched from the
            # encrypted store only here, at the moment of connecting.
            if token := cfg.auth_token(self.id):
                headers["Authorization"] = f"Bearer {token}"
            url = str(conf.get("url", ""))
            if transport == "sse":
                from mcp.client.sse import sse_client

                read, write = await stack.enter_async_context(
                    sse_client(url, headers=headers)
                )
            else:
                from mcp.client.streamable_http import streamablehttp_client

                # streamable-http yields a third element (a session-id callback) that
                # ClientSession doesn't take.
                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(url, headers=headers)
                )
        session = await stack.enter_async_context(ClientSession(read, write))
        return session

    async def _run(self) -> None:
        """Own the session for its whole lifetime: connect, discover, park, tear down."""
        try:
            async with AsyncExitStack() as stack:
                session = await self._open(stack)
                info = await session.initialize()
                self._session = session
                self.runtime.server_name = info.serverInfo.name
                self.runtime.server_version = info.serverInfo.version
                await self._discover(session, info)
                self.runtime.state = "ready"
                self.runtime.error = None
                self._ready.set()
                logger.info(
                    "mcp: %s ready (%d tools, %d prompts)",
                    self.id,
                    len(self.runtime.tools),
                    len(self.runtime.prompts),
                )
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure becomes visible status
            self.runtime.state = "error"
            self.runtime.error = f"{type(exc).__name__}: {exc}"
            logger.warning("mcp: %s failed: %s", self.id, self.runtime.error)
        finally:
            self._session = None
            # Always release start()'s waiter, success or not, so a failed server
            # reports its error instead of hanging until the connect timeout.
            self._ready.set()

    async def _discover(
        self, session: ClientSession, info: mcp_types.InitializeResult
    ) -> None:
        """Read the server's catalog once, tolerating unsupported capabilities.

        A server that declares no `prompts`/`resources` capability raises on those
        calls; that is normal and must not fail the connection.
        """
        caps = info.capabilities
        self.runtime.tools = []
        self.runtime.prompts = []
        self.runtime.resources = []

        if caps.tools is not None:
            listed = await session.list_tools()
            self.runtime.tools = [
                ToolInfo(
                    name=t.name,
                    description=(t.description or "").strip(),
                    input_schema=t.inputSchema or {"type": "object", "properties": {}},
                    read_only=bool(
                        getattr(t.annotations, "readOnlyHint", False) or False
                    ),
                    destructive=bool(
                        getattr(t.annotations, "destructiveHint", False) or False
                    ),
                )
                for t in listed.tools
            ]

        if caps.prompts is not None:
            try:
                prompts = await session.list_prompts()
                self.runtime.prompts = [
                    PromptInfo(name=p.name, description=(p.description or "").strip())
                    for p in prompts.prompts
                ]
            except Exception as exc:  # noqa: BLE001
                logger.debug("mcp: %s list_prompts failed: %s", self.id, exc)

        if caps.resources is not None:
            try:
                resources = await session.list_resources()
                self.runtime.resources = [
                    ResourceInfo(
                        uri=str(r.uri),
                        name=r.name or str(r.uri),
                        description=(r.description or "").strip(),
                        mime_type=r.mimeType,
                    )
                    for r in resources.resources
                ]
            except Exception as exc:  # noqa: BLE001
                logger.debug("mcp: %s list_resources failed: %s", self.id, exc)

        # `instructions` is the server's own description of how to use it — exactly
        # what the agent's group-guide slot wants.
        self.runtime.instructions = (info.instructions or "").strip()

    async def call_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool on this server; return a JSON-able result for the agent."""
        if self._session is None or self.runtime.state != "ready":
            return {"error": f"MCP server '{self.id}' is not connected"}
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool, args), timeout=CALL_TIMEOUT_S
            )
        except TimeoutError:
            return {"error": f"MCP tool '{tool}' timed out after {CALL_TIMEOUT_S:.0f}s"}
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as a result
            return {"error": f"{type(exc).__name__}: {exc}"}
        return _flatten_result(result)

    async def read_resource(self, uri: str) -> dict[str, Any]:
        if self._session is None or self.runtime.state != "ready":
            return {"error": f"MCP server '{self.id}' is not connected"}
        try:
            result = await asyncio.wait_for(
                self._session.read_resource(mcp_types.AnyUrl(uri)),
                timeout=CALL_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
        parts: list[dict[str, Any]] = []
        for content in result.contents:
            if isinstance(content, mcp_types.TextResourceContents):
                parts.append({"uri": str(content.uri), "text": content.text})
            else:
                parts.append({"uri": str(content.uri), "blob": "<binary omitted>"})
        return {"contents": parts}


def _flatten_result(result: mcp_types.CallToolResult) -> dict[str, Any]:
    """An MCP tool result reduced to something a local model can read.

    MCP returns a typed content list (text / image / embedded resource). Models do far
    better with a plain string, so text parts are joined and non-text parts are named
    rather than inlined — a base64 image dumped into the context would blow the window
    for no benefit.
    """
    texts: list[str] = []
    extras: list[str] = []
    for content in result.content:
        if isinstance(content, mcp_types.TextContent):
            texts.append(content.text)
        elif isinstance(content, mcp_types.ImageContent):
            extras.append(f"<image {content.mimeType}>")
        else:
            extras.append(f"<{type(content).__name__}>")
    out: dict[str, Any] = {"content": "\n".join(texts).strip()}
    if extras:
        out["attachments"] = extras
    # `structuredContent` is the machine-readable half when a server declares an
    # output schema; pass it through untouched.
    if getattr(result, "structuredContent", None):
        out["structured"] = result.structuredContent
    if result.isError:
        out["error"] = out.pop("content", "") or "tool reported an error"
    return out


class McpManager:
    """Process-global registry of MCP sessions, keyed by server id."""

    def __init__(self) -> None:
        self.sessions: dict[str, McpSession] = {}

    def get(self, server_id: str) -> McpSession | None:
        return self.sessions.get(server_id)

    def runtimes(self) -> list[ServerRuntime]:
        """Live runtime for every configured server, including ones never started."""
        out: list[ServerRuntime] = []
        for conf in cfg.list_servers():
            sid = str(conf.get("id", ""))
            session = self.sessions.get(sid)
            out.append(session.runtime if session else ServerRuntime(config=conf))
        return out

    async def start_server(self, server_id: str) -> ServerRuntime | None:
        conf = cfg.get_server(server_id)
        if conf is None:
            return None
        existing = self.sessions.get(server_id)
        if existing is not None:
            await existing.stop()
        session = McpSession(conf)
        self.sessions[server_id] = session
        await session.start()
        _rebuild_bridge()
        return session.runtime

    async def stop_server(self, server_id: str) -> None:
        session = self.sessions.pop(server_id, None)
        if session is not None:
            await session.stop()
        _rebuild_bridge()

    async def start_enabled(self) -> None:
        """Start every enabled server. Called from the app lifespan.

        Servers start concurrently — a slow `npx` download must not serialize behind
        another, and one failure must not stop the rest.
        """
        configs = [c for c in cfg.list_servers() if c.get("enabled", True)]
        if not configs:
            return
        await asyncio.gather(
            *(self.start_server(str(c.get("id", ""))) for c in configs),
            return_exceptions=True,
        )

    async def stop_all(self) -> None:
        await asyncio.gather(
            *(s.stop() for s in list(self.sessions.values())), return_exceptions=True
        )
        self.sessions.clear()


def _rebuild_bridge() -> None:
    """Re-register agent tools after any change to what's connected."""
    from backend.modules.mcp import bridge

    bridge.sync(manager)


# One manager per backend process; the routes, the lifespan, and the bridge share it.
manager = McpManager()
