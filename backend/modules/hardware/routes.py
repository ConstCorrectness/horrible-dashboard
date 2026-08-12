"""HTTP surface for the hardware probe."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from backend.modules.hardware import probe as probe_mod
from backend.modules.hardware.models import HardwareModel

router = APIRouter(prefix="/hardware", tags=["hardware"])


def _payload(profile: probe_mod.Profile) -> HardwareModel:
    return HardwareModel.model_validate(
        {
            "profile": profile.to_dict(),
            "defaults": probe_mod.defaults(profile).to_dict(),
        }
    )


@router.get("", response_model=HardwareModel)
async def get_hardware() -> HardwareModel:
    # `to_thread` because the first call runs the probe, which spawns
    # subprocesses; every later call is a cache read and returns immediately.
    profile = await asyncio.to_thread(probe_mod.get_profile)
    return _payload(profile)


@router.post("/refresh", response_model=HardwareModel)
async def refresh_hardware() -> HardwareModel:
    """Re-run the probe. Also the way a settings override takes effect."""
    profile = await asyncio.to_thread(probe_mod.get_profile, refresh=True)
    return _payload(profile)
