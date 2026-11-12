"""The `horrible-module.json` contract, Python side.

Mirrors `packages/sdk/src/module.ts` (`ModulePackageManifest`) and the JSON Schema in
`packages/sdk/schema/horrible-module.schema.json`. A module package is one directory
with a web half, a Python half, or both; this is the manifest that ties them
together. A legacy `horrible-plugin.json` reads into the same shape, so the module
loader and the plugin loader agree on what a package is.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODULE_MANIFEST = "horrible-module.json"
LEGACY_PLUGIN_MANIFEST = "horrible-plugin.json"

#: Oldest and newest contract versions this backend accepts (`SDK_API_VERSION`).
MIN_SDK_VERSION = 3
SDK_VERSION = 4

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CONTRACT = re.compile(r"^[a-zA-Z0-9.-]+@[0-9]+$")
_PY_ENTRY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")


class ManifestError(ValueError):
    """A manifest that cannot describe a loadable module. The message names the field."""


@dataclass(frozen=True)
class ToolGroup:
    id: str
    description: str
    always_on: bool = False


@dataclass(frozen=True)
class PySpec:
    root: str
    package: str
    entry: str


@dataclass(frozen=True)
class WebSpec:
    entry: str
    dist: str | None = None


@dataclass(frozen=True)
class Standalone:
    pane: str
    port: int = 0
    title: str | None = None


@dataclass(frozen=True)
class ModuleManifest:
    id: str
    name: str
    version: str
    sdk_version: int
    description: str = ""
    author: str = ""
    web: WebSpec | None = None
    py: PySpec | None = None
    depends_on: dict[str, str] = field(default_factory=dict)
    provides: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    tool_groups: tuple[ToolGroup, ...] = ()
    ws_channels: tuple[str, ...] = ()
    panes: tuple[str, ...] = ()
    renamed_panes: dict[str, str] = field(default_factory=dict)
    standalone: Standalone | None = None
    required_capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()

    def owns_tool(self, tool_name: str) -> bool:
        """Whether `tool_name`'s group (the text before the first dot) is declared."""
        group = tool_name.split(".", 1)[0]
        return any(g.id == group for g in self.tool_groups)

    def to_json(self) -> dict[str, Any]:
        """The camelCase wire shape the frontend reads (`ModulePackageManifest`)."""
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "sdkVersion": self.sdk_version,
            "dependsOn": dict(self.depends_on),
            "provides": list(self.provides),
            "requires": list(self.requires),
            "toolGroups": [
                {"id": g.id, "description": g.description, "alwaysOn": g.always_on}
                for g in self.tool_groups
            ],
            "wsChannels": list(self.ws_channels),
            "panes": list(self.panes),
            "renamedPanes": dict(self.renamed_panes),
            "requiredCapabilities": list(self.required_capabilities),
            "permissions": list(self.permissions),
        }
        if self.web:
            out["web"] = {
                "entry": self.web.entry,
                **({"dist": self.web.dist} if self.web.dist else {}),
            }
        if self.py:
            out["py"] = {
                "root": self.py.root,
                "package": self.py.package,
                "entry": self.py.entry,
            }
        if self.standalone:
            out["standalone"] = {
                "pane": self.standalone.pane,
                "port": self.standalone.port,
                **({"title": self.standalone.title} if self.standalone.title else {}),
            }
        return out


def _str_list(raw: Any, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ManifestError(f"{field_name} must be a list of strings")
    return tuple(raw)


def _str_map(raw: Any, field_name: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
    ):
        raise ManifestError(f"{field_name} must be an object of strings")
    return dict(raw)


def parse_manifest(data: dict[str, Any]) -> ModuleManifest:
    """Validate and parse a manifest dict (either shape). Raises `ManifestError`."""
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a JSON object")
    data = dict(data)
    # Legacy horrible-plugin.json: a prebuilt web bundle at `entry`.
    if isinstance(data.get("entry"), str) and "web" not in data:
        entry = data.pop("entry")
        data["web"] = {"entry": entry, "dist": entry}

    mid = data.get("id")
    if not isinstance(mid, str) or not _ID.match(mid):
        raise ManifestError(f"id {mid!r} must match {_ID.pattern}")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ManifestError("name is required")
    sdk = data.get("sdkVersion")
    if not isinstance(sdk, int) or isinstance(sdk, bool):
        raise ManifestError("sdkVersion must be an integer")
    if not MIN_SDK_VERSION <= sdk <= SDK_VERSION:
        raise ManifestError(
            f"built for SDK v{sdk}; this app accepts v{MIN_SDK_VERSION}–v{SDK_VERSION}"
        )

    web = None
    if (w := data.get("web")) is not None:
        if not isinstance(w, dict) or not isinstance(w.get("entry"), str):
            raise ManifestError("web.entry is required")
        dist = w.get("dist")
        web = WebSpec(entry=w["entry"], dist=dist if isinstance(dist, str) else None)

    py = None
    if (p := data.get("py")) is not None:
        if not isinstance(p, dict) or not all(
            isinstance(p.get(k), str) for k in ("root", "package", "entry")
        ):
            raise ManifestError("py needs root, package and entry")
        if not _PY_ENTRY.match(p["entry"]):
            raise ManifestError("py.entry must look like 'package.module:ATTRIBUTE'")
        py = PySpec(root=p["root"], package=p["package"], entry=p["entry"])

    contracts = {k: _str_list(data.get(k), k) for k in ("provides", "requires")}
    for k, ids in contracts.items():
        for cid in ids:
            if not _CONTRACT.match(cid):
                raise ManifestError(f"{k}: {cid!r} must look like 'name@<major>'")

    groups: list[ToolGroup] = []
    for g in data.get("toolGroups") or []:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str):
            raise ManifestError("toolGroups entries need an id")
        groups.append(
            ToolGroup(
                id=g["id"],
                description=str(g.get("description", "")),
                always_on=bool(g.get("alwaysOn", False)),
            )
        )

    standalone = None
    if (s := data.get("standalone")) is not None:
        if not isinstance(s, dict) or not isinstance(s.get("pane"), str):
            raise ManifestError("standalone.pane is required")
        standalone = Standalone(
            pane=s["pane"], port=int(s.get("port") or 0), title=s.get("title")
        )

    return ModuleManifest(
        id=mid,
        name=name,
        version=str(data.get("version", "0.0.0")),
        sdk_version=sdk,
        description=str(data.get("description", "")),
        author=str(data.get("author", "")),
        web=web,
        py=py,
        depends_on=_str_map(data.get("dependsOn"), "dependsOn"),
        provides=contracts["provides"],
        requires=contracts["requires"],
        tool_groups=tuple(groups),
        ws_channels=_str_list(data.get("wsChannels"), "wsChannels"),
        panes=_str_list(data.get("panes"), "panes"),
        renamed_panes=_str_map(data.get("renamedPanes"), "renamedPanes"),
        standalone=standalone,
        required_capabilities=_str_list(
            data.get("requiredCapabilities"), "requiredCapabilities"
        ),
        permissions=_str_list(data.get("permissions"), "permissions"),
    )


def read_manifest(package_dir: Path) -> ModuleManifest | None:
    """The manifest in `package_dir` (module manifest preferred), or None if it has none.
    Raises `ManifestError` for a manifest that exists but is invalid, and also when the
    id does not equal the directory name (the id *is* the install location)."""
    for name in (MODULE_MANIFEST, LEGACY_PLUGIN_MANIFEST):
        path = package_dir / name
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ManifestError(f"{path}: {exc}") from exc
            manifest = parse_manifest(data)
            if manifest.id != package_dir.name:
                raise ManifestError(
                    f"id {manifest.id!r} must equal its directory name {package_dir.name!r}"
                )
            return manifest
    return None
