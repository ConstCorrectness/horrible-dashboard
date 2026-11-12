"""`/api/modules`: list, enable/disable (live), and install/uninstall module packages.

Module packages (`horrible-module.json`) are the successor to plugin packages: a web
half, a Python half, or both. Enabling or disabling one loads or unloads it in the
running backend and announces it on the `/ws` `modules` channel, so every open
dashboard adds or removes its panes and tools without a reload.

Returns plain dicts rather than `response_model`s on purpose: the manifest grows, and
a response model silently drops fields it doesn't know.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.kernel.module_host import ModuleError
from backend.kernel.module_manager import module_manager
from backend.sdk.module_manifest import ManifestError, read_manifest

router = APIRouter(prefix="/modules", tags=["modules"])

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class EnabledBody(BaseModel):
    enabled: bool


class InstallBody(BaseModel):
    id: str


def _catalog_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_MODULE_CATALOG", "examples/modules"))


def _check_id(module_id: str) -> None:
    if not _ID.match(module_id):
        raise HTTPException(status_code=422, detail=f"invalid module id {module_id!r}")


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("")
def list_modules() -> dict[str, Any]:
    return module_manager.describe_all()


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    root = _catalog_dir()
    out: list[dict[str, Any]] = []
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                manifest = read_manifest(entry)
            except ManifestError:
                continue
            if manifest is not None:
                out.append(manifest.to_json())
    return {"modules": out}


@router.put("/{module_id}/enabled")
async def set_enabled(module_id: str, body: EnabledBody) -> dict[str, Any]:
    _check_id(module_id)
    try:
        return await module_manager.set_enabled(module_id, body.enabled)
    except ModuleError as exc:
        raise _conflict(exc) from exc


@router.post("/install")
async def install(body: InstallBody) -> dict[str, Any]:
    _check_id(body.id)
    source = _catalog_dir() / body.id
    if not source.is_dir():
        raise HTTPException(
            status_code=404, detail=f"{body.id!r} is not in the catalog"
        )
    try:
        return await module_manager.install(source)
    except ModuleError as exc:
        raise _conflict(exc) from exc


@router.delete("/{module_id}")
async def uninstall(module_id: str) -> dict[str, Any]:
    _check_id(module_id)
    try:
        await module_manager.uninstall(module_id)
    except ModuleError as exc:
        raise _conflict(exc) from exc
    return {"ok": True}
