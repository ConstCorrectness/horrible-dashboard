"""HTTP surface for the audio module.

Two halves that are easy to confuse, so they are kept at separate paths:

- `/audio/mixer` is the **dashboard's own** routing matrix — durable storage for
  a graph that lives in the browser. The backend never interprets it.
- `/audio/host` is the **machine's** mixer (Voicemeeter), which the backend
  drives directly and which affects every application on the box, not just this
  one.

Everything that touches a driver goes through `asyncio.to_thread`: the Remote API
DLL blocks, and `pactl` is a subprocess.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from backend.modules.audio import events, providers, store, voicemeeter
from backend.modules.audio.models import (
    AudioStatusModel,
    CreateDeviceRequest,
    HostMixerModel,
    LaunchRequest,
    MixerStateModel,
    ProviderStatusModel,
    SetGainRequest,
    SetSendRequest,
    VirtualDeviceModel,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])


def _status_payload() -> AudioStatusModel:
    provider = providers.get_provider()
    status = provider.status()
    host: HostMixerModel | None = None
    host_error: str | None = None
    if status.can_control:
        try:
            host = HostMixerModel.model_validate(voicemeeter.read_state().to_dict())
        except voicemeeter.VoicemeeterError as exc:
            # Reported, never swallowed: "the matrix isn't showing" with no
            # explanation is exactly the failure mode this module is built to
            # avoid. It is also not a 500 — the rest of the status is valid.
            host_error = str(exc)
    return AudioStatusModel(
        provider=ProviderStatusModel.model_validate(status.to_dict()),
        host=host,
        hostError=host_error,
    )


@router.get("/status", response_model=AudioStatusModel)
async def get_status() -> AudioStatusModel:
    """What this machine can do about audio routing, and what the host mixer is
    currently set to."""
    return await asyncio.to_thread(_status_payload)


# ---------------------------------------------------------------------------
# The dashboard's own mixer
# ---------------------------------------------------------------------------


@router.get("/mixer", response_model=MixerStateModel)
async def get_mixer() -> MixerStateModel:
    return MixerStateModel.model_validate(await asyncio.to_thread(store.load_state))


@router.put("/mixer", response_model=MixerStateModel)
async def put_mixer(state: MixerStateModel) -> MixerStateModel:
    saved = await asyncio.to_thread(store.save_state, state.model_dump())
    # Broadcast so a *second* mixer pane (or a phone on the fabric) reconciles
    # instead of continuing to render a routing that is no longer in effect. The
    # pane that sent this will see its own echo and no-op on it.
    await events.publish_mixer(saved)
    return MixerStateModel.model_validate(saved)


@router.delete("/mixer", response_model=MixerStateModel)
async def delete_mixer() -> MixerStateModel:
    """Back to defaults: one bus on the system output, nothing routed away."""
    reset = await asyncio.to_thread(store.reset_state)
    await events.publish_mixer(reset)
    return MixerStateModel.model_validate(reset)


# ---------------------------------------------------------------------------
# The host mixer (Voicemeeter)
# ---------------------------------------------------------------------------


@router.post("/host/launch", response_model=AudioStatusModel)
async def launch_host(request: LaunchRequest) -> AudioStatusModel:
    """Start the host mixer.

    Deliberately explicit rather than automatic on first status read: launching
    Voicemeeter takes over the machine's default audio devices, which is a
    visible, disruptive change to every running application. It happens when
    someone asks for it.
    """
    if not voicemeeter.is_installed():
        raise HTTPException(
            status_code=404, detail="Voicemeeter is not installed on this machine"
        )
    try:
        await asyncio.to_thread(voicemeeter.launch, request.kindId)
    except voicemeeter.VoicemeeterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return await asyncio.to_thread(_status_payload)


@router.post("/host/send", response_model=HostMixerModel)
async def set_host_send(request: SetSendRequest) -> HostMixerModel:
    """Flip one cell of the host matrix — "send strip 3 to B1"."""
    try:
        await asyncio.to_thread(
            voicemeeter.set_send, request.strip, request.bus, request.enabled
        )
        return HostMixerModel.model_validate(
            (await asyncio.to_thread(voicemeeter.read_state)).to_dict()
        )
    except voicemeeter.VoicemeeterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/host/level", response_model=HostMixerModel)
async def set_host_level(request: SetGainRequest) -> HostMixerModel:
    """Set gain and/or mute on one strip or bus of the host mixer."""
    if request.target not in ("strip", "bus"):
        raise HTTPException(status_code=422, detail="target must be 'strip' or 'bus'")
    try:
        if request.gainDb is not None:
            setter = (
                voicemeeter.set_strip_gain
                if request.target == "strip"
                else voicemeeter.set_bus_gain
            )
            await asyncio.to_thread(setter, request.index, request.gainDb)
        if request.muted is not None:
            setter = (
                voicemeeter.set_strip_mute
                if request.target == "strip"
                else voicemeeter.set_bus_mute
            )
            await asyncio.to_thread(setter, request.index, request.muted)
        return HostMixerModel.model_validate(
            (await asyncio.to_thread(voicemeeter.read_state)).to_dict()
        )
    except voicemeeter.VoicemeeterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Virtual devices
# ---------------------------------------------------------------------------


@router.post("/devices", response_model=VirtualDeviceModel)
async def create_device(request: CreateDeviceRequest) -> VirtualDeviceModel:
    """Create a virtual cable. Only Linux can do this; everywhere else the
    answer is an install, and saying so is more useful than a 500."""
    provider = providers.get_provider()
    try:
        device = await asyncio.to_thread(provider.create, request.label)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return VirtualDeviceModel.model_validate(device.to_dict())


@router.delete("/devices/{device_id}")
async def delete_device(device_id: str) -> dict[str, bool]:
    """Remove a cable the dashboard created. Refuses anything it did not."""
    provider = providers.get_provider()
    try:
        await asyncio.to_thread(provider.destroy, device_id)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}
