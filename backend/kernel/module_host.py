"""What a module's Python half gets: `ModuleHost`, the `HorribleModule` base, and the
route mounter that lets a module's HTTP routes come and go while the app runs.

A module is a `BackendPlugin` with a `horrible-module.json` behind it. The host it
receives is a `PluginHost` (so every plugin capability is available) plus what a
module needs to be *unloadable*: background tasks the host owns and cancels, a
`/ws` disconnect hook instead of a line in `app.py`, and typed services instead of
importing another module.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute

from backend.sdk.host import BackendPlugin, PluginHost
from backend.sdk.module_manifest import ModuleManifest
from backend.sdk.registry import PluginRegistry, _MountedRouter
from backend.sdk.types import AgentTool, PluginManifest, WsDisconnectHook

logger = logging.getLogger(__name__)

BackgroundFactory = Callable[[], Awaitable[Any]]


class ModuleError(RuntimeError):
    """A module that cannot load, unload or register what it asked to. The message is
    shown to the user in the marketplace, so it names the module and the reason."""


class HorribleModule(BackendPlugin):
    """Base for a module's Python half. Expose an instance as ``MODULE``.

    `setup` must be safe to call again after `teardown`: an unload followed by a load
    re-runs it on a fresh host in the same process (Python cannot un-import code, so
    module globals survive). Start long-running work with `host.background`, never a
    bare `asyncio.create_task`, so an unload can stop it."""

    #: Filled from ``horrible-module.json`` by the manager before `setup` runs.
    module_manifest: ModuleManifest | None = None

    def setup(self, host: ModuleHost) -> None:  # type: ignore[override]
        raise NotImplementedError

    async def teardown(self) -> None:
        """Release what `setup` built that the host does not own. Optional."""


class ModuleHost(PluginHost):
    """The surface handed to `HorribleModule.setup`."""

    def __init__(
        self, manifest: ModuleManifest, reg: PluginRegistry | None = None
    ) -> None:
        super().__init__(
            PluginManifest(
                id=manifest.id,
                name=manifest.name,
                version=manifest.version,
                description=manifest.description,
            ),
            reg,
        )
        self.module = manifest
        self.log = logging.getLogger(f"module.{manifest.id}")
        self._background: list[BackgroundFactory] = []

    # --- routes -----------------------------------------------------------------

    def add_router(self, router: APIRouter, *, prefix: str | None = None) -> None:
        """Mount a router under `/api`. Unlike a plugin, a module's routes keep the
        router's own prefix by default (`/api/karaoke/...` stays `/api/karaoke/...`)."""
        super().add_router(router, prefix=prefix if prefix is not None else "")

    # --- agent tools ------------------------------------------------------------

    def add_agent_tool(self, tool: AgentTool) -> None:
        """Register a tool in one of the module's declared `toolGroups`.

        A module tool is never silently always-on: an undeclared group raises (the
        manifest is the catalogue the agent reads), and a tool with no `group` is put
        in its name's group unless that group is declared `alwaysOn`."""
        group = tool.name.split(".", 1)[0]
        declared = {g.id: g for g in self.module.tool_groups}
        if group not in declared:
            raise ModuleError(
                f"module {self.module.id!r}: tool {tool.name!r} is outside its declared "
                f"toolGroups {sorted(declared) or '[]'}"
            )
        if tool.group is None and not declared[group].always_on:
            tool = dataclasses.replace(tool, group=group)
        super().add_agent_tool(tool)

    # --- lifetime ---------------------------------------------------------------

    def background(self, factory: BackgroundFactory) -> None:
        """Run `factory()` as a task while the module is loaded; cancelled on unload.

        A factory rather than a coroutine, so a reload can start it again."""
        self._background.append(factory)

    def on_ws_disconnect(self, hook: WsDisconnectHook) -> None:
        """Called with the connection when a `/ws` socket closes."""
        self._registry.ws_disconnect_hooks.append(hook)
        self._own("ws_disconnect_hooks", hook)

    # --- talking to the browser and to other modules -----------------------------

    async def broadcast(
        self, event: str, data: dict[str, Any], *, channel: str | None = None
    ) -> None:
        """Send `event` to every `/ws` connection on `channel` (default: the module id)."""
        from backend.modules.ws import broadcast_event

        await broadcast_event(channel or self.module.id, event, data)

    def provide(self, contract: str, impl: Any) -> None:
        """Offer a typed service other modules can `require`. Withdrawn on unload."""
        if contract not in self.module.provides:
            self.log.warning(
                "provides %s without declaring it in horrible-module.json", contract
            )
        self._registry.provide(self.owner_id, contract, impl)

    def require(self, contract: str) -> Any | None:
        """The service providing `contract`, or None if no loaded module provides it.

        Look it up when you need it, not once in `setup`: the provider can be unloaded
        and reloaded independently of this module."""
        return self._registry.service(contract)

    # --- storage ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        """A directory this module owns under the node's data dir (created on use)."""
        from backend import paths

        path = paths.data_dir() / "module-data" / self.module.id
        path.mkdir(parents=True, exist_ok=True)
        return path


class RouteMounter:
    """Adds a module's routes to the running app and takes them back out.

    FastAPI routes are a list on the app's router; `include_router` appends to it.
    So mounting records exactly which route objects one include added, and unmounting
    removes those objects. Everything else about the app — middleware, exception
    handlers, `request.app`, dependency overrides — applies to module routes exactly
    as to built-in ones, which a separately mounted sub-app would not give us."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._mounted: dict[str, list[Any]] = {}

    @staticmethod
    def _signature(route: Any) -> set[tuple[str, str]]:
        if isinstance(route, APIRoute):
            return {(route.path, m) for m in route.methods or ()}
        if isinstance(route, APIWebSocketRoute):
            return {(route.path, "WS")}
        return set()

    def mount(self, owner: str, mounted: list[_MountedRouter]) -> None:
        """Include every router `owner` registered. Refuses (and mounts nothing) if any
        of its routes would collide with one already served."""
        if not mounted:
            return
        probe = FastAPI()
        for m in mounted:
            probe.include_router(m.router, prefix=f"/api{m.prefix}")
        incoming: set[tuple[str, str]] = set()
        for r in probe.router.routes:
            incoming |= self._signature(r)
        taken: set[tuple[str, str]] = set()
        for r in self._app.router.routes:
            taken |= self._signature(r)
        clash = sorted(incoming & taken)
        if clash:
            path, method = clash[0]
            raise ModuleError(
                f"module {owner!r}: {method} {path} is already served"
                + (f" (and {len(clash) - 1} more)" if len(clash) > 1 else "")
            )
        routes = self._app.router.routes
        before = len(routes)
        for m in mounted:
            self._app.include_router(m.router, prefix=f"/api{m.prefix}")
        self._mounted.setdefault(owner, []).extend(routes[before:])
        self._app.openapi_schema = None

    def unmount(self, owner: str) -> int:
        added = self._mounted.pop(owner, [])
        if not added:
            return 0
        ids = {id(r) for r in added}
        self._app.router.routes[:] = [
            r for r in self._app.router.routes if id(r) not in ids
        ]
        self._app.openapi_schema = None
        return len(added)

    def mounted(self, owner: str) -> int:
        return len(self._mounted.get(owner, []))


async def start_background(
    owner: str, factories: list[BackgroundFactory]
) -> list[asyncio.Task[Any]]:
    tasks: list[asyncio.Task[Any]] = []
    for factory in factories:
        try:
            tasks.append(asyncio.create_task(factory(), name=f"module:{owner}"))
        except Exception:  # noqa: BLE001 — one bad task must not stop the module loading
            logger.exception("module %s: background task failed to start", owner)
    return tasks


async def cancel_tasks(tasks: list[asyncio.Task[Any]]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 — it is going away
            pass
