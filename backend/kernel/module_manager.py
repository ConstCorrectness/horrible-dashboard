"""The module lifecycle: discover → enable → load, and back out, while the app runs.

States, per module id::

    available (in a catalog) → installed (on disk) → enabled (persisted) → loaded (live)

`enabled` is a persisted *block*list (`<data>/modules-state.json`), so a module that
ships in a new build is on by default. `loaded` is this process: the module's routes
are served, its tools are in the registry, its background tasks run. A web-only
module has nothing to load here; its `loaded` follows `enabled` and the browser does
the rest when the `/ws` `modules` event says so.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from backend import jsonstore
from backend.kernel.module_host import (
    HorribleModule,
    ModuleError,
    ModuleHost,
    RouteMounter,
    cancel_tasks,
    start_background,
)
from backend.sdk.host import BackendPlugin
from backend.sdk.module_manifest import ManifestError, read_manifest
from backend.sdk.module_scan import (
    ModuleDescriptor,
    installed_modules_dir,
    load_order,
    scan_modules,
)
from backend.sdk.registry import PluginRegistry, registry

logger = logging.getLogger(__name__)

WS_CHANNEL = "modules"


def _state_path() -> Path:
    from backend import paths

    return paths.data_dir() / "modules-state.json"


@dataclass
class _Live:
    """A loaded module's runtime handles."""

    instance: BackendPlugin | None
    host: ModuleHost | None
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)


class _HeldByPeer(ModuleError):
    """Another live process on the bus hosts this module."""


class ModuleManager:
    def __init__(self, reg: PluginRegistry | None = None) -> None:
        self._registry = reg if reg is not None else registry
        self._app: FastAPI | None = None
        self._mounter: RouteMounter | None = None
        self._descriptors: dict[str, ModuleDescriptor] = {}
        self._live: dict[str, _Live] = {}
        self._errors: dict[str, str] = {}
        self._scan_errors: list[tuple[str, str]] = []
        self._started = False
        self._lock = asyncio.Lock()
        # Directories to scan instead of the defaults (tests, the standalone runner).
        self.scan_kwargs: dict[str, Any] = {}
        # The standalone runner loads only these (plus what they depend on).
        self.only: set[str] | None = None
        # Set by the bus. `claim` takes a module for this process before its setup
        # runs and returns None, or names the live peer that holds it ("<name> (pid
        # N)"). One process owns a module's state: a second load is refused rather than
        # allowed to write it too. `release` gives it back on unload.
        self.claim: Callable[[str], str | None] | None = None
        self.release: Callable[[str], None] | None = None
        # Enabled modules refused because a peer held them; retried when it leaves.
        self._deferred: set[str] = set()
        self._listeners: list[Callable[[], None]] = []

    # --- wiring -----------------------------------------------------------------

    def attach(self, app: FastAPI) -> None:
        self._app = app
        self._mounter = RouteMounter(app)

    def _installed_dir(self) -> Path:
        """Where `install` puts packages — the same directory discovery scans."""
        return self.scan_kwargs.get("installed_dir") or installed_modules_dir()

    def discover(self) -> None:
        result = scan_modules(**self.scan_kwargs)
        self._descriptors = result.modules
        self._scan_errors = result.errors
        for where, msg in result.errors:
            logger.warning("module package at %s skipped: %s", where, msg)

    # --- persisted enable state ----------------------------------------------------

    def disabled(self) -> set[str]:
        raw = jsonstore.read_text(_state_path())
        if not raw:
            return set()
        try:
            return set(json.loads(raw).get("disabled", []))
        except (ValueError, AttributeError):
            return set()

    def _write_disabled(self, ids: set[str]) -> None:
        path = _state_path()
        with jsonstore.locked(path):
            jsonstore.write_text(path, json.dumps({"disabled": sorted(ids)}, indent=2))

    def is_enabled(self, module_id: str) -> bool:
        # In a standalone runner "enabled" means "one this process runs", whatever the
        # shared blocklist says: disabling karaoke in the dashboard so it can run on
        # its own is exactly what lets the runner take it.
        if self.only is not None:
            return module_id in self._closure(self.only)
        return module_id not in self.disabled()

    # --- reads --------------------------------------------------------------------

    def descriptor(self, module_id: str) -> ModuleDescriptor:
        d = self._descriptors.get(module_id)
        if d is None:
            raise ModuleError(f"no module {module_id!r}")
        return d

    def is_loaded(self, module_id: str) -> bool:
        return module_id in self._live

    def live_ids(self) -> list[str]:
        return list(self._live)

    def add_listener(self, fn: Callable[[], None]) -> None:
        """Called after any module loads or unloads (the bus republishes on it)."""
        self._listeners.append(fn)

    def _closure(self, ids: set[str]) -> set[str]:
        """`ids` plus everything they depend on, transitively."""
        out: set[str] = set()
        stack = list(ids)
        while stack:
            mid = stack.pop()
            if mid in out or mid not in self._descriptors:
                continue
            out.add(mid)
            stack.extend(self._descriptors[mid].manifest.depends_on)
        return out

    def tool_group_description(self, group: str) -> str | None:
        """The `toolGroups` blurb a loaded module declared for `group`, if any. Feeds
        the agent's group catalog, which is how the model decides to load a group."""
        for mid in self._live:
            for g in self._descriptors[mid].manifest.tool_groups:
                if g.id == group:
                    return g.description
        return None

    def dependents(self, module_id: str) -> list[str]:
        """Loaded modules that hard-depend (`dependsOn`) on `module_id`.

        `requires` is deliberately not counted: a contract user must tolerate its
        provider being absent, so unloading the provider is allowed."""
        return [
            mid
            for mid in self._live
            if mid != module_id
            and module_id in self._descriptors[mid].manifest.depends_on
        ]

    def describe(self, module_id: str) -> dict[str, Any]:
        d = self.descriptor(module_id)
        enabled = self.is_enabled(module_id)
        return {
            **d.manifest.to_json(),
            "builtin": d.builtin,
            "source": d.source,
            "enabled": enabled,
            # A web-only module is "loaded" whenever it is enabled — the browser loads it.
            "loaded": self.is_loaded(module_id) if d.manifest.py else enabled,
            "error": self._errors.get(module_id),
        }

    def describe_all(self) -> dict[str, Any]:
        return {
            "modules": [self.describe(mid) for mid in self._descriptors],
            "errors": [{"where": w, "message": m} for w, m in self._scan_errors],
        }

    # --- load / unload ---------------------------------------------------------------

    def load_all(self) -> None:
        """Synchronous boot load (before the lifespan starts) of every enabled module,
        dependencies first. A failure is recorded against that module and boot goes on."""
        disabled = self.disabled()
        wanted = self._closure(self.only) if self.only is not None else None
        candidates = [
            d.manifest
            for d in self._descriptors.values()
            if (d.id in wanted if wanted is not None else d.id not in disabled)
        ]
        order, problems = load_order(candidates)
        for p in problems:
            logger.warning("module load order: %s", p)
        for mid in order:
            try:
                self._setup(mid)
            except _HeldByPeer as exc:
                self._errors[mid] = str(exc)
                logger.info("module %s not loaded: %s", mid, exc)
            except Exception as exc:  # noqa: BLE001 — one module never takes boot down
                self._errors[mid] = f"{type(exc).__name__}: {exc}"
                logger.exception("module %s failed to load", mid)

    def _setup(self, module_id: str) -> None:
        if module_id in self._live:
            return
        d = self.descriptor(module_id)
        missing = [
            dep
            for dep in d.manifest.depends_on
            if dep not in self._live
            and dep in self._descriptors
            and self._descriptors[dep].manifest.py
        ]
        if missing:
            raise ModuleError(
                f"module {module_id!r} needs {', '.join(missing)} loaded first"
            )
        claimed = False
        if d.manifest.py and self.claim is not None:
            if holder := self.claim(module_id):
                self._deferred.add(module_id)
                raise _HeldByPeer(
                    f"{module_id} is running in {holder}; it loads here when that stops"
                )
            claimed = True
        self._deferred.discard(module_id)
        try:
            self._setup_claimed(module_id, d)
        except BaseException:
            if claimed and self.release is not None:
                self.release(module_id)
            raise

    def _setup_claimed(self, module_id: str, d: ModuleDescriptor) -> None:
        obj = d.load_python()
        if obj is None:
            self._errors.pop(module_id, None)
            return
        instance = obj() if isinstance(obj, type) else obj
        if not isinstance(instance, BackendPlugin):
            raise ModuleError(
                f"module {module_id!r}: {d.manifest.py.entry} is not a HorribleModule"
            )  # type: ignore[union-attr]
        if isinstance(instance, HorribleModule):
            instance.module_manifest = d.manifest
        host = ModuleHost(d.manifest, self._registry)
        try:
            instance.setup(host)  # type: ignore[arg-type]
            if self._mounter is not None:
                mine = [
                    r
                    for kind, r in self._registry.owned.get(module_id, [])
                    if kind == "routers"
                ]
                self._mounter.mount(module_id, mine)
        except Exception:
            self._registry.unregister_owner(module_id)
            if self._mounter is not None:
                self._mounter.unmount(module_id)
            raise
        self._live[module_id] = _Live(instance=instance, host=host)
        self._errors.pop(module_id, None)
        logger.info("module %s loaded (%s)", module_id, d.source)

    async def start(self) -> None:
        """Called from the app lifespan once startup hooks have run: start every loaded
        module's background tasks. Modules loaded later start their own."""
        self._started = True
        for mid, live in self._live.items():
            if live.host is not None:
                live.tasks = await start_background(mid, live.host._background)

    async def stop(self) -> None:
        for mid in list(reversed(list(self._live))):
            # Each module runs its own shutdown hooks here, in reverse load order:
            # `_teardown` unregisters them, so the registry's global run_shutdown
            # afterwards no longer sees them.
            await self._teardown(mid, run_shutdown=True)
        self._started = False

    async def load(self, module_id: str) -> dict[str, Any]:
        """Load one module into the running app."""
        async with self._lock:
            if module_id not in self._live:
                try:
                    self._setup(module_id)
                except Exception as exc:
                    self._errors[module_id] = (
                        str(exc)
                        if isinstance(exc, _HeldByPeer)
                        else f"{type(exc).__name__}: {exc}"
                    )
                    raise
                live = self._live.get(module_id)
                if self._started and live is not None and live.host is not None:
                    await self._registry.run_startup(owner=module_id)
                    live.tasks = await start_background(
                        module_id, live.host._background
                    )
            await self._announce(module_id)
            return self.describe(module_id)

    async def unload(self, module_id: str) -> dict[str, Any]:
        """Take a module out of the running app. Refuses while a loaded module depends on it."""
        async with self._lock:
            deps = self.dependents(module_id)
            if deps:
                raise ModuleError(
                    f"{', '.join(deps)} depend(s) on {module_id!r}; disable them first"
                )
            await self._teardown(module_id, run_shutdown=True)
            await self._announce(module_id)
            return self.describe(module_id)

    async def _teardown(self, module_id: str, *, run_shutdown: bool) -> None:
        live = self._live.pop(module_id, None)
        if live is None:
            return
        await cancel_tasks(live.tasks)
        if run_shutdown:
            await self._registry.run_shutdown(owner=module_id)
        if isinstance(live.instance, HorribleModule):
            try:
                await live.instance.teardown()
            except Exception:  # noqa: BLE001 — unloading must finish
                logger.exception("module %s teardown failed", module_id)
        if self._mounter is not None:
            self._mounter.unmount(module_id)
        self._registry.unregister_owner(module_id)
        if self.release is not None:
            self.release(module_id)
        logger.info("module %s unloaded", module_id)

    async def set_enabled(self, module_id: str, enabled: bool) -> dict[str, Any]:
        d = self.descriptor(module_id)
        if enabled:
            missing = [dep for dep in d.manifest.depends_on if not self.is_enabled(dep)]
            if missing:
                raise ModuleError(
                    f"enable {', '.join(missing)} first: {module_id!r} depends on it"
                )
        else:
            deps = [
                mid
                for mid in self._descriptors
                if module_id in self._descriptors[mid].manifest.depends_on
                and self.is_enabled(mid)
            ]
            if deps:
                raise ModuleError(
                    f"{', '.join(deps)} depend(s) on {module_id!r}; disable them first"
                )
        disabled = self.disabled()
        disabled.discard(module_id) if enabled else disabled.add(module_id)
        self._write_disabled(disabled)
        if d.manifest.py:
            return await (self.load(module_id) if enabled else self.unload(module_id))
        await self._announce(module_id)
        return self.describe(module_id)

    # --- install / uninstall (third-party packages) -------------------------------------

    async def install(self, source: Path) -> dict[str, Any]:
        """Copy a module package directory into `<data>/modules/<id>` and load it."""
        try:
            manifest = read_manifest(source)
        except ManifestError as exc:
            raise ModuleError(str(exc)) from exc
        if manifest is None:
            raise ModuleError(f"{source} has no horrible-module.json")
        existing = self._descriptors.get(manifest.id)
        if existing is not None and existing.builtin:
            raise ModuleError(f"{manifest.id!r} is built in")
        dest = self._installed_dir() / manifest.id
        if self.is_loaded(manifest.id):
            await self.unload(manifest.id)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(
            source,
            dest,
            ignore=shutil.ignore_patterns("node_modules", ".git", "__pycache__"),
        )
        self.discover()
        if self.is_enabled(manifest.id) and manifest.py:
            return await self.load(manifest.id)
        await self._announce(manifest.id)
        return self.describe(manifest.id)

    async def uninstall(self, module_id: str) -> None:
        d = self.descriptor(module_id)
        if d.source != "installed" or d.path is None:
            raise ModuleError(
                f"{module_id!r} is not an installed package (it is {d.source})"
            )
        await self.unload(module_id)
        shutil.rmtree(d.path)
        self.discover()
        from backend.modules.ws import broadcast_event

        await broadcast_event(WS_CHANNEL, "removed", {"id": module_id})

    async def retry_deferred(self) -> list[str]:
        """Load the enabled modules a peer held, now that it may have stopped."""
        loaded = []
        for mid in sorted(self._deferred):
            if mid in self._live or not self.is_enabled(mid):
                self._deferred.discard(mid)
                continue
            if self.only is not None and mid not in self._closure(self.only):
                continue
            try:
                await self.load(mid)
                loaded.append(mid)
            except _HeldByPeer:
                pass
            except Exception:  # noqa: BLE001 — recorded in describe()
                logger.exception("module %s failed to load", mid)
        return loaded

    async def _announce(self, module_id: str) -> None:
        from backend.modules.ws import broadcast_event

        for fn in list(self._listeners):
            try:
                fn()
            except Exception:  # noqa: BLE001 — a listener never fails a load
                logger.exception("module listener failed")
        await broadcast_event(WS_CHANNEL, "state", self.describe(module_id))


#: The process's one manager.
module_manager = ModuleManager()
