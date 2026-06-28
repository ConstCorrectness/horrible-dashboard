"""Example backend plugin exercising the whole `backend.sdk` surface.

It registers one of everything — an HTTP route, an agent tool, a `/ws` channel, a
`dash` facade, and startup/shutdown hooks — so it doubles as the reference plugin
and the integration test fixture. Drop a package shaped like this into
``backend/plugins/`` (bundled) or a ``HORRIBLE_PLUGINS_DIR`` directory, or ship it
as a pip package with a ``horrible.plugins`` entry point. See
docs/architecture/python-sdk.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.sdk import AgentTool, BackendPlugin, PluginHost, PluginManifest

# Observable side effects, so a test can prove the hooks ran.
events: list[str] = []

router = APIRouter()


@router.get("")
def ping() -> dict[str, Any]:
    """GET /api/plugins/ping → a friendly pong."""
    return {"pong": True, "plugin": "ping"}


async def echo_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Agent tool: echo back the given text."""
    return {"echo": args.get("text", "")}


async def ping_channel(conn: Any, message: dict[str, Any]) -> None:
    """`/ws` channel `ping`: reply to any frame with a pong."""
    await conn.send_json(
        {"channel": "ping", "event": "pong", "data": message.get("data")}
    )


class _PingFacade:
    """`dash.ping` — the plugin's REPL handle."""

    def pong(self) -> str:
        """Return 'pong'."""
        return "pong"


class PingPlugin(BackendPlugin):
    manifest = PluginManifest(
        id="ping",
        name="Ping",
        version="1.0.0",
        description="Reference plugin: route + agent tool + ws channel + dash facade.",
    )

    def setup(self, host: PluginHost) -> None:
        host.add_router(router)  # mounts at /api/plugins/ping
        host.add_agent_tool(
            AgentTool(
                name="ping.echo",
                description="Echo back the given text.",
                handler=echo_tool,
                parameters={"text": {"type": "string", "description": "text to echo"}},
                required=["text"],
            )
        )
        host.add_ws_channel("ping", ping_channel)
        host.add_dash_facade("ping", _PingFacade)
        host.on_startup(lambda: events.append("startup"))
        host.on_shutdown(lambda: events.append("shutdown"))


PLUGIN = PingPlugin()
