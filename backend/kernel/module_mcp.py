"""Serve this process's module tools over MCP, for the other processes on the bus.

Every horrible process that hosts modules mounts one of these at `/bus/mcp`. It lists
exactly the agent tools owned by the modules loaded *here*, computed on every
request, so a module that is disabled stops being callable on the next call rather
than at the next restart. Tool names are unchanged (`karaoke.status`), so a peer that
mounts them gets the same group (`karaoke`) the module would have had in-process.

This is a different server from `backend/modules/mcp/server.py`, which exports the
dashboard's read-only trajectory introspection to outside tools at `/mcp-server`.
This one runs *module* tools, some of which act, so:

- Bearer token, per process, published only in the bus record (`bus.py`).
- DNS-rebinding protection from the SDK: the Host header must be loopback, and a
  request carrying any non-loopback `Origin` is refused. A web page that resolves
  its own name to 127.0.0.1 can reach the port; it cannot get past either check.
- `destructiveHint` mirrors `side_effect`, and the *calling* process runs its own
  permission gate before it calls (the bridge keeps `side_effect` on the mirrored
  tool). This server does not prompt: it has no user to ask.

Lifespan: Starlette does not run a mounted sub-app's lifespan, so the host's lifespan
enters `run()` itself (the same trap `mcp/server.py` documents).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any

from backend.sdk.registry import PluginRegistry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

GROUP_META = "horrible/group"
SPECIFIER_META = "horrible/specifierTemplate"

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def _input_schema(tool: AgentTool) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(tool.parameters),
        "required": list(tool.required),
    }


def encode_result(result: Any) -> str:
    """A tool's return value as the text the peer parses back (`decode_result`)."""
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str)


class ModuleMcpServer:
    """One process's MCP endpoint over the tools its loaded modules own."""

    def __init__(
        self,
        reg: PluginRegistry,
        owners: Callable[[], Iterable[str]],
        *,
        name: str,
        token: str,
    ) -> None:
        self._reg = reg
        self._owners = owners
        self._name = name
        self._token = token
        self._manager: Any = None

    # --- what is exposed ----------------------------------------------------------

    def exposed(self) -> list[AgentTool]:
        """Tools owned by a loaded module here. Never a bus-mirrored tool: a process
        relays nothing it does not run itself, so calls cannot loop between peers."""
        owners = set(self._owners())
        out = []
        for name, tool in self._reg.agent_tools.items():
            if self._reg.owner_of_tool(name) in owners:
                out.append(tool)
        return sorted(out, key=lambda t: t.name)

    def descriptors(self) -> list[dict[str, Any]]:
        """What the bus record hashes, so peers re-list only when this changes."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "schema": _input_schema(t),
                "sideEffect": t.side_effect,
                "group": t.group,
            }
            for t in self.exposed()
        ]

    # --- the server ---------------------------------------------------------------

    def _build(self) -> Any:
        import mcp.types as types
        from mcp.server.lowlevel import Server

        server: Any = Server(self._name)

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [
                types.Tool(
                    name=t.name,
                    description=t.description,
                    inputSchema=_input_schema(t),
                    annotations=types.ToolAnnotations(
                        readOnlyHint=not t.side_effect,
                        destructiveHint=t.side_effect,
                    ),
                    _meta={
                        GROUP_META: t.group or t.name.split(".", 1)[0],
                        SPECIFIER_META: t.specifier_template,
                    },
                )
                for t in self.exposed()
            ]

        # validate_input off: the tool's own handler is the authority on its args, as
        # it is for an in-process call. Validating here as well would make a remote
        # call stricter than a local one for the same tool.
        @server.call_tool(validate_input=False)
        async def call_tool(
            name: str, arguments: dict[str, Any]
        ) -> list[types.TextContent]:
            if name not in {t.name for t in self.exposed()}:
                raise ValueError(f"no tool '{name}' on {self._name}")
            result = await self._reg.invoke_agent_tool(name, arguments or {})
            return [types.TextContent(type="text", text=encode_result(result))]

        return server

    def asgi(self) -> Any:
        """The guarded ASGI app to mount at `/bus` (the endpoint is `/bus/mcp`)."""
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings

        from backend.modules.mcp.auth import BearerAuthMiddleware

        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{h}:*" for h in _LOOPBACK_HOSTS] + list(_LOOPBACK_HOSTS),
            allowed_origins=[f"http://{h}:*" for h in _LOOPBACK_HOSTS],
        )
        self._manager = StreamableHTTPSessionManager(
            app=self._build(),
            json_response=True,
            stateless=True,
            security_settings=security,
        )
        manager = self._manager

        async def handle(scope: Any, receive: Any, send: Any) -> None:
            await manager.handle_request(scope, receive, send)

        return BearerAuthMiddleware(handle, token=self._token)

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """Enter from the host app's lifespan, after `asgi()` is mounted."""
        if self._manager is None:
            yield
            return
        async with self._manager.run():
            yield
