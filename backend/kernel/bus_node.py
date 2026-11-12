"""Put one process on the module bus: publish, serve, mirror.

The dashboard and every standalone runner do the same three things, so they share
this class:

1. **Publish** a record (`bus.BusPublisher`) naming the modules loaded here and a
   hash of their tools.
2. **Serve** those tools over MCP at `/bus/mcp` (`module_mcp.ModuleMcpServer`).
3. **Mirror** every peer's tools into the local registry (`bus_bridge.BusBridge`).

And it enforces the one rule that spans processes: a module is loaded in at most one
of them. `ModuleManager.peer_owner` refuses a local load while a live peer hosts the
module; when the peer leaves, the bridge notices and `retry_deferred` loads it here.

Set `HORRIBLE_BUS=off` to keep a process off the bus entirely (tests set it).
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from starlette.routing import Mount

from backend.kernel import bus
from backend.kernel.bus_bridge import BusBridge
from backend.kernel.module_mcp import ModuleMcpServer
from backend.sdk.registry import PluginRegistry

logger = logging.getLogger(__name__)

BUS_ENV = "HORRIBLE_BUS"


def bus_enabled() -> bool:
    return os.environ.get(BUS_ENV, "").strip().lower() not in {"off", "0", "false"}


class BusNode:
    def __init__(
        self,
        manager: Any,
        reg: PluginRegistry,
        *,
        kind: str,
        name: str,
        port: int | Callable[[], int],
    ) -> None:
        self.manager = manager
        self.reg = reg
        self.publisher = bus.BusPublisher(
            kind=kind, name=name, port=port, describe=self._describe
        )
        self.server = ModuleMcpServer(
            reg, manager.live_ids, name=f"horrible-{name}", token=self.publisher.token
        )
        self.bridge = BusBridge(reg, on_change=self._peers_changed)
        self._exit: AsyncExitStack | None = None

    # --- wiring --------------------------------------------------------------------

    def attach(self, app: Any) -> None:
        """Mount the MCP endpoint and hook the manager. Call before the app starts."""
        app.router.routes.append(Mount("/bus", app=self.server.asgi()))
        self.manager.claim = bus.claim
        self.manager.release = bus.release
        self.manager.add_listener(self._local_changed)

    def _describe(self) -> dict[str, Any]:
        live = self.manager.live_ids()
        groups: list[dict[str, str]] = []
        for mid in live:
            for g in self.manager.descriptor(mid).manifest.tool_groups:
                groups.append({"id": g.id, "description": g.description})
        return {
            "modules": live,
            "toolGroups": groups,
            "toolsHash": bus.tools_hash(self.server.descriptors()),
        }

    def _local_changed(self) -> None:
        # A module loaded or unloaded here: tell peers now rather than next beat, and
        # re-list peers, since a name this process just released may be theirs to
        # mirror (or a name it just took must stop being mirrored).
        try:
            self.publisher.publish_now()
        except Exception:  # noqa: BLE001
            logger.debug("bus: republish failed", exc_info=True)
        self.bridge.resync()

    def _peers_changed(self) -> None:
        # A peer left, or its modules or tools changed: either way a module this
        # process deferred may be free now (a dashboard that disabled karaoke so it
        # can run standalone stays alive; only its record changes). A no-op when
        # nothing is deferred.
        import asyncio

        asyncio.get_running_loop().create_task(self.manager.retry_deferred())

    def group_description(self, group: str) -> str | None:
        return self.bridge.group_description(group)

    # --- lifetime ------------------------------------------------------------------

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """Enter from the app lifespan, after modules have loaded."""
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(self.server.run())
            self.publisher.start()
            stack.push_async_callback(self.publisher.stop)
            self.bridge.start()
            stack.push_async_callback(self.bridge.stop)
            logger.info("bus: joined as %s", self.publisher.record.name)
            yield


#: The process's node, once a host app has created one.
node: BusNode | None = None


def install(
    app: Any, manager: Any, reg: PluginRegistry, **kwargs: Any
) -> BusNode | None:
    """Create and attach the process's node, unless the bus is switched off."""
    global node
    if not bus_enabled():
        return None
    node = BusNode(manager, reg, **kwargs)
    node.attach(app)
    return node
