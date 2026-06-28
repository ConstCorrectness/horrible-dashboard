"""Public backend plugin SDK (`backend.sdk`).

Import the contract from here to write a backend plugin::

    from backend.sdk import AgentTool, BackendPlugin, PluginHost, PluginManifest

    class MyPlugin(BackendPlugin):
        manifest = PluginManifest(id="my-plugin", name="My Plugin")

        def setup(self, host: PluginHost) -> None:
            host.add_router(my_router)
            host.add_agent_tool(AgentTool(name="my.tool", description="…", handler=...))

    PLUGIN = MyPlugin()

See docs/architecture/python-sdk.md for the full surface and trust model.
"""

from backend.sdk.host import BackendPlugin, PluginHost
from backend.sdk.loader import load_plugins
from backend.sdk.registry import PluginRegistry, registry
from backend.sdk.types import (
    AgentTool,
    AgentToolHandler,
    DashFacadeFactory,
    LifecycleHook,
    PluginManifest,
    WsChannelHandler,
)

__all__ = [
    "AgentTool",
    "AgentToolHandler",
    "BackendPlugin",
    "DashFacadeFactory",
    "LifecycleHook",
    "PluginHost",
    "PluginManifest",
    "PluginRegistry",
    "WsChannelHandler",
    "load_plugins",
    "registry",
]
