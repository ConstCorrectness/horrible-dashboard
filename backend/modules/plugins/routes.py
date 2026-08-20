"""Plugin lifecycle: catalog, install/uninstall/enable, assets, KV storage.

Installed plugins live under `$HORRIBLE_DATA_DIR/plugins/<id>/`:
  package/       verbatim copy of the catalog package (manifest + dist/)
  state.json     {"enabled": bool}
  storage.json   plugin-scoped key-value store

The catalog is a directory of plugin packages (`HORRIBLE_PLUGIN_CATALOG`,
default `examples/plugins`) — each subdirectory holds a `horrible-plugin.json`.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import ValidationError

from backend import jsonstore, paths
from backend.modules.plugins.models import (
    MANIFEST_FILENAME,
    PLUGIN_ID_PATTERN,
    STORAGE_KEY_PATTERN,
    CatalogResponse,
    EnabledRequest,
    InstalledListResponse,
    InstalledPlugin,
    InstallRequest,
    OkResponse,
    PluginManifest,
    StorageEntry,
    StorageValue,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])

PluginId = Annotated[str, PathParam(pattern=PLUGIN_ID_PATTERN)]
StorageKey = Annotated[str, PathParam(pattern=STORAGE_KEY_PATTERN)]


def _catalog_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_PLUGIN_CATALOG", "examples/plugins"))


def _plugins_root() -> Path:
    return paths.data_dir() / "plugins"


def _package_dir(plugin_id: str) -> Path:
    return _plugins_root() / plugin_id / "package"


def _read_manifest(package_dir: Path, expected_id: str) -> PluginManifest | None:
    """Parse a package's manifest; None (with a log line) when missing/invalid.

    `expected_id` ties the manifest to its directory name so a package can't
    claim another plugin's id.
    """
    manifest_path = package_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        manifest = PluginManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (ValidationError, ValueError) as err:
        logger.warning("Skipping plugin package %s: %s", package_dir, err)
        return None
    if manifest.id != expected_id:
        logger.warning(
            "Skipping plugin package %s: manifest id %r != directory %r",
            package_dir,
            manifest.id,
            expected_id,
        )
        return None
    return manifest


def _read_enabled(plugin_id: str) -> bool:
    text = jsonstore.read_text(_plugins_root() / plugin_id / "state.json")
    if text is None:
        return True
    try:
        state = json.loads(text)
    except ValueError:
        return True
    return bool(state.get("enabled", True))


def _write_enabled(plugin_id: str, enabled: bool) -> None:
    jsonstore.write_text(
        _plugins_root() / plugin_id / "state.json", json.dumps({"enabled": enabled})
    )


def _storage_path(plugin_id: str) -> Path:
    return _plugins_root() / plugin_id / "storage.json"


def _read_storage(plugin_id: str) -> dict:
    text = jsonstore.read_text(_storage_path(plugin_id))
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_storage(plugin_id: str, data: dict) -> None:
    jsonstore.write_text(_storage_path(plugin_id), json.dumps(data))


def _require_installed(plugin_id: str) -> PluginManifest:
    manifest = _read_manifest(_package_dir(plugin_id), expected_id=plugin_id)
    if manifest is None:
        raise HTTPException(
            status_code=404, detail=f"Plugin '{plugin_id}' is not installed"
        )
    return manifest


@router.get("/catalog", response_model=CatalogResponse)
def catalog() -> CatalogResponse:
    catalog_dir = _catalog_dir()
    plugins: list[PluginManifest] = []
    if catalog_dir.is_dir():
        for child in sorted(catalog_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest = _read_manifest(child, expected_id=child.name)
            if manifest is not None:
                plugins.append(manifest)
    return CatalogResponse(plugins=plugins)


@router.get("/installed", response_model=InstalledListResponse)
def installed() -> InstalledListResponse:
    root = _plugins_root()
    plugins: list[InstalledPlugin] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            manifest = _read_manifest(child / "package", expected_id=child.name)
            if manifest is not None:
                plugins.append(
                    InstalledPlugin(
                        manifest=manifest, enabled=_read_enabled(child.name)
                    )
                )
    return InstalledListResponse(plugins=plugins)


@router.post("/install", response_model=InstalledPlugin)
def install(req: InstallRequest) -> InstalledPlugin:
    src = _catalog_dir() / req.id
    manifest = _read_manifest(src, expected_id=req.id)
    if manifest is None:
        raise HTTPException(
            status_code=404, detail=f"Plugin '{req.id}' not found in catalog"
        )
    dest = _package_dir(req.id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # dirs_exist_ok makes reinstalling an update-in-place.
    shutil.copytree(
        src,
        dest,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("node_modules", ".git"),
    )
    _write_enabled(req.id, True)
    return InstalledPlugin(manifest=manifest, enabled=True)


@router.delete("/{plugin_id}", response_model=OkResponse)
def uninstall(plugin_id: PluginId) -> OkResponse:
    target = _plugins_root() / plugin_id
    if not target.is_dir():
        raise HTTPException(
            status_code=404, detail=f"Plugin '{plugin_id}' is not installed"
        )
    # Removes the plugin's storage too — uninstall is a full reset by design.
    shutil.rmtree(target)
    return OkResponse()


@router.put("/{plugin_id}/enabled", response_model=InstalledPlugin)
def set_enabled(plugin_id: PluginId, req: EnabledRequest) -> InstalledPlugin:
    manifest = _require_installed(plugin_id)
    _write_enabled(plugin_id, req.enabled)
    return InstalledPlugin(manifest=manifest, enabled=req.enabled)


@router.get("/{plugin_id}/assets/{asset_path:path}")
def asset(plugin_id: PluginId, asset_path: str) -> FileResponse:
    base = _package_dir(plugin_id).resolve()
    target = (base / asset_path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    # Windows `mimetypes` can map .js to text/plain via the registry, and
    # browsers refuse ES module imports with a non-JS MIME — force it.
    media_type = "text/javascript" if target.suffix in {".js", ".mjs"} else None
    return FileResponse(target, media_type=media_type)


@router.get("/{plugin_id}/storage/{key}", response_model=StorageEntry)
def storage_get(plugin_id: PluginId, key: StorageKey) -> StorageEntry:
    data = _read_storage(plugin_id)
    if key not in data:
        raise HTTPException(status_code=404, detail=f"No value for key '{key}'")
    return StorageEntry(key=key, value=data[key])


@router.put("/{plugin_id}/storage/{key}", response_model=StorageEntry)
def storage_put(
    plugin_id: PluginId, key: StorageKey, body: StorageValue
) -> StorageEntry:
    _require_installed(plugin_id)
    # One plugin writing two keys at once must not lose one: the store is a whole
    # JSON document per plugin, so both writers would read the same dict and the
    # second would write it back without the first key. Locked per plugin file,
    # inline rather than via `jsonstore.serialized`, because the path depends on
    # an argument.
    with jsonstore.locked(_storage_path(plugin_id)):
        data = _read_storage(plugin_id)
        data[key] = body.value
        _write_storage(plugin_id, data)
    return StorageEntry(key=key, value=body.value)


@router.delete("/{plugin_id}/storage/{key}", response_model=OkResponse)
def storage_delete(plugin_id: PluginId, key: StorageKey) -> OkResponse:
    with jsonstore.locked(_storage_path(plugin_id)):
        data = _read_storage(plugin_id)
        if key in data:
            del data[key]
            _write_storage(plugin_id, data)
    return OkResponse()
