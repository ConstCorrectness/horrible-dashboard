"""HTTP surface for the terminal: which shells this machine can launch.

The sessions themselves live on the `terminal` WS channel (`manager.py`); this is
only the picker's catalog. See docs/modules/terminal.mdx.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from backend.modules.terminal import shells as shells_mod

router = APIRouter(prefix="/terminal", tags=["terminal"])


class ShellModel(BaseModel):
    id: str
    label: str
    kind: str
    #: Display only. It is never accepted back — `start` carries an id.
    path: str
    note: str | None = None


class ShellsModel(BaseModel):
    shells: list[ShellModel]
    #: The id a session with no `shell` will get, or null when the platform default
    #: is not one of the discovered entries (it still launches; it is just unnamed).
    default: str | None = None


async def _payload() -> ShellsModel:
    found = await asyncio.to_thread(shells_mod.discover_shells)
    default = await asyncio.to_thread(shells_mod.default_shell_id)
    return ShellsModel(
        shells=[ShellModel.model_validate(s.to_dict()) for s in found],
        default=default,
    )


@router.get("/shells", response_model=ShellsModel)
async def get_shells() -> ShellsModel:
    # `to_thread` because the first call probes (it may shell out to `wsl.exe`);
    # every later call is a cache read.
    return await _payload()


@router.post("/shells/refresh", response_model=ShellsModel)
async def refresh_shells() -> ShellsModel:
    """Re-probe. The way a shell installed mid-session becomes selectable."""
    await asyncio.to_thread(shells_mod.reset_cache)
    return await _payload()
