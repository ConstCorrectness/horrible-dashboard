"""Find module packages and order them for loading.

Four sources, first match per id wins:

1. ``<repo-or-runtime>/modules/*``  — built-ins, shipped with the app
2. ``HORRIBLE_MODULES_PATH``        — extra directories (``os.pathsep``-separated)
3. ``<data>/modules/*``             — installed third-party modules
4. ``horrible.modules`` entry points — pip-installed modules

A directory module's Python half is imported from ``<dir>/<py.root>`` when its
package is not already importable, so a module checked out as a git submodule, or
copied into a packaged runtime, works with no pip install.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from backend.sdk.module_manifest import ManifestError, ModuleManifest, read_manifest

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "horrible.modules"
ENV_MODULES_PATH = "HORRIBLE_MODULES_PATH"


@dataclass
class ModuleDescriptor:
    """A discovered module package: its manifest and where it came from."""

    manifest: ModuleManifest
    #: The package directory; None for an entry-point module.
    path: Path | None
    source: str  # "builtin" | "path" | "installed" | "entry_point"
    #: Entry-point modules resolve their Python object at discovery time.
    _entry_obj: Any = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def builtin(self) -> bool:
        return self.source == "builtin"

    def load_python(self) -> Any | None:
        """Import and return the module's Python entry object (``MODULE``), or None
        for a web-only module."""
        if self._entry_obj is not None:
            return self._entry_obj
        py = self.manifest.py
        if py is None:
            return None
        module_path, _, attr = py.entry.partition(":")
        try:
            importlib.import_module(py.package)
        except ModuleNotFoundError:
            if self.path is None:
                raise
            root = str((self.path / py.root).resolve())
            if root not in sys.path:
                sys.path.insert(0, root)
        return getattr(importlib.import_module(module_path), attr)


@dataclass
class ScanResult:
    modules: dict[str, ModuleDescriptor] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)


def builtin_modules_dir() -> Path:
    """`modules/` beside `backend/` — in a checkout and in the packaged runtime alike."""
    return Path(__file__).resolve().parents[2] / "modules"


def installed_modules_dir() -> Path:
    from backend import paths

    return paths.data_dir() / "modules"


def _scan_dir(directory: Path, source: str, out: ScanResult) -> None:
    if not directory.is_dir():
        return
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        try:
            manifest = read_manifest(entry)
        except ManifestError as exc:
            out.errors.append((str(entry), str(exc)))
            continue
        if manifest is None or manifest.id in out.modules:
            continue
        out.modules[manifest.id] = ModuleDescriptor(manifest, entry, source)


def _scan_entry_points(out: ScanResult) -> None:
    try:
        eps = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 — metadata API varies across versions
        return
    for ep in eps:
        if ep.name in out.modules:
            continue
        try:
            obj = ep.load()
            manifest = getattr(obj, "module_manifest", None)
            if not isinstance(manifest, ModuleManifest):
                raise ManifestError("entry point object has no `module_manifest`")
        except Exception as exc:  # noqa: BLE001
            out.errors.append(
                (f"entry_point:{ep.name}", f"{type(exc).__name__}: {exc}")
            )
            continue
        out.modules[manifest.id] = ModuleDescriptor(
            manifest, None, "entry_point", _entry_obj=obj
        )


def scan_modules(
    *,
    builtin_dir: Path | None = None,
    extra_dirs: Iterable[Path] = (),
    installed_dir: Path | None = None,
    entry_points: bool = True,
) -> ScanResult:
    """Discover every module package. Never raises: bad packages land in `errors`."""
    out = ScanResult()
    _scan_dir(builtin_dir or builtin_modules_dir(), "builtin", out)
    env = [
        Path(p.strip())
        for p in os.environ.get(ENV_MODULES_PATH, "").split(os.pathsep)
        if p.strip()
    ]
    for directory in [*env, *extra_dirs]:
        _scan_dir(directory, "path", out)
    _scan_dir(installed_dir or installed_modules_dir(), "installed", out)
    if entry_points:
        _scan_entry_points(out)
    return out


def load_order(manifests: Iterable[ModuleManifest]) -> tuple[list[str], list[str]]:
    """Topologically order modules so dependencies load first.

    Edges come from `dependsOn` and from `requires` → whichever module `provides` that
    contract. Ties keep input order, so the result is deterministic. Returns
    ``(order, problems)``: a cycle does not fail the load — its members are appended
    in input order and named in `problems`, because a running app with a logged cycle
    beats a blank one. Dependencies on modules that aren't present are reported too.
    """
    items = list(manifests)
    ids = [m.id for m in items]
    present = set(ids)
    providers: dict[str, str] = {}
    for m in items:
        for c in m.provides:
            providers.setdefault(c, m.id)

    deps: dict[str, set[str]] = {}
    problems: list[str] = []
    for m in items:
        d: set[str] = set()
        for dep in m.depends_on:
            if dep in present:
                d.add(dep)
            else:
                problems.append(f"{m.id} depends on missing module {dep!r}")
        for c in m.requires:
            p = providers.get(c)
            if p and p != m.id:
                d.add(p)
        deps[m.id] = d

    order: list[str] = []
    done: set[str] = set()
    remaining = list(ids)
    while remaining:
        ready = [i for i in remaining if deps[i] <= done]
        if not ready:
            problems.append("dependency cycle among: " + ", ".join(remaining))
            order.extend(remaining)
            break
        for i in ready:
            order.append(i)
            done.add(i)
        remaining = [i for i in remaining if i not in done]
    return order, problems
