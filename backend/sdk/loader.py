"""Discover and load backend plugins.

Two sources (both enabled):

* **Directory scan** — packages/modules under ``backend/plugins/`` (bundled) and any
  directory on the ``HORRIBLE_PLUGINS_DIR`` env var (``os.pathsep``-separated). Each
  plugin is a package or ``.py`` file exposing a module-level ``PLUGIN``.
* **Entry points** — pip-installed packages declaring a ``horrible.plugins`` entry
  point (``importlib.metadata``) that resolves to a ``BackendPlugin`` (instance,
  class, or a module exposing ``PLUGIN``).

A plugin that fails to import or set up is recorded in ``registry.errors`` and
skipped — one bad plugin never takes the app down. See
docs/architecture/python-sdk.md.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from importlib import metadata
from pathlib import Path
from typing import Any

from backend.sdk.host import BackendPlugin, PluginHost
from backend.sdk.registry import PluginRegistry, registry

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "horrible.plugins"
ENV_PLUGINS_DIR = "HORRIBLE_PLUGINS_DIR"
BUNDLED_DIR = Path(__file__).resolve().parent.parent / "plugins"


def _coerce_plugin(obj: Any) -> BackendPlugin | None:
    """Resolve a discovered object to a BackendPlugin instance: a module exposing
    ``PLUGIN``, a plugin class, or an instance."""
    if hasattr(obj, "PLUGIN"):
        obj = obj.PLUGIN
    if isinstance(obj, type) and issubclass(obj, BackendPlugin):
        obj = obj()
    return obj if isinstance(obj, BackendPlugin) else None


def _setup_plugin(plugin: BackendPlugin, source: str, reg: PluginRegistry) -> None:
    """Run one plugin's setup, recording its manifest or its failure."""
    try:
        host = PluginHost(plugin.manifest, reg)
        plugin.setup(host)
        reg.loaded.append(plugin.manifest)
        logger.info("loaded backend plugin %s (%s)", plugin.manifest.id, source)
    except Exception as exc:  # noqa: BLE001 — one bad plugin must not crash the app
        reg.errors.append((source, f"{type(exc).__name__}: {exc}"))
        logger.exception("backend plugin setup failed (%s)", source)


def _load_from_module(module: Any, source: str, reg: PluginRegistry) -> None:
    plugin = _coerce_plugin(module)
    if plugin is None:
        reg.errors.append((source, "no PLUGIN (BackendPlugin) found in module"))
        return
    _setup_plugin(plugin, source, reg)


def _scan_directory(directory: Path, reg: PluginRegistry) -> None:
    """Load every package/`.py` plugin directly under `directory`."""
    if not directory.is_dir():
        return
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        init = entry / "__init__.py" if entry.is_dir() else None
        target = (
            init
            if init and init.is_file()
            else (entry if entry.suffix == ".py" else None)
        )
        if target is None:
            continue
        mod_name = f"_horrible_plugin_{directory.name}_{entry.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, target)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            reg.errors.append((str(target), f"{type(exc).__name__}: {exc}"))
            logger.exception("failed importing plugin at %s", target)
            continue
        _load_from_module(module, str(target), reg)


def _scan_bundled(reg: PluginRegistry) -> None:
    """Bundled plugins import as real `backend.plugins.<name>` packages."""
    if not BUNDLED_DIR.is_dir():
        return
    for entry in sorted(BUNDLED_DIR.iterdir()):
        if entry.name.startswith((".", "_")) or not entry.is_dir():
            continue
        if not (entry / "__init__.py").is_file():
            continue
        name = f"backend.plugins.{entry.name}"
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            reg.errors.append((name, f"{type(exc).__name__}: {exc}"))
            logger.exception("failed importing bundled plugin %s", name)
            continue
        _load_from_module(module, name, reg)


def _load_entry_points(reg: PluginRegistry) -> None:
    try:
        entries = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 — metadata API varies across versions
        return
    for ep in entries:
        try:
            plugin = _coerce_plugin(ep.load())
        except Exception as exc:  # noqa: BLE001
            reg.errors.append(
                (f"entry_point:{ep.name}", f"{type(exc).__name__}: {exc}")
            )
            logger.exception("failed loading entry-point plugin %s", ep.name)
            continue
        if plugin is None:
            reg.errors.append(
                (f"entry_point:{ep.name}", "did not resolve to a BackendPlugin")
            )
            continue
        _setup_plugin(plugin, f"entry_point:{ep.name}", reg)


def load_plugins(
    *, extra_dirs: list[Path] | None = None, reg: PluginRegistry | None = None
) -> PluginRegistry:
    """Discover and set up every backend plugin into `reg` (the global registry by
    default). Idempotent per registry: call `reg.reset()` first to reload. Returns the
    registry for convenience."""
    reg = reg if reg is not None else registry
    _scan_bundled(reg)
    for raw in os.environ.get(ENV_PLUGINS_DIR, "").split(os.pathsep):
        if raw.strip():
            _scan_directory(Path(raw.strip()), reg)
    for directory in extra_dirs or []:
        _scan_directory(directory, reg)
    _load_entry_points(reg)
    return reg
