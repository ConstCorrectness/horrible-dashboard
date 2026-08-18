"""API models for the audio module.

Every field the adapters produce is mirrored here on purpose: a `response_model`
filters anything it does not declare, **silently**, so a field added to
`ProviderStatus` and forgotten here reaches the browser as `undefined` with no
error on either side.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VirtualDeviceModel(BaseModel):
    id: str
    name: str
    #: "sink" (an app writes to it) | "source" (an app records from it) |
    #: "duplex" (the pair, exposed under one name).
    kind: str
    owned: bool = False


class ProviderStatusModel(BaseModel):
    platform: str
    provider: str | None = None
    installed: bool
    running: bool
    #: False means *we could not ask*. Never the same fact as `installed=False` —
    #: the UI must say "unknown", never "not installed".
    certain: bool = True
    canCreate: bool = False
    canControl: bool = False
    note: str = ""
    installName: str = ""
    installUrl: str | None = None
    devices: list[VirtualDeviceModel] = Field(default_factory=list)


class StripModel(BaseModel):
    index: int
    name: str
    label: str
    isVirtual: bool
    gain: float
    muted: bool
    #: Bus name → on. A bus absent from this map is one the running mixer does
    #: not have, which is not the same as a send that is switched off.
    sends: dict[str, bool] = Field(default_factory=dict)


class BusModel(BaseModel):
    index: int
    name: str
    label: str
    isVirtual: bool
    gain: float
    muted: bool


class HostMixerModel(BaseModel):
    """The OS-level mixer (Voicemeeter), when one is running and controllable."""

    kind: str
    kindId: int
    version: str
    strips: list[StripModel] = Field(default_factory=list)
    buses: list[BusModel] = Field(default_factory=list)


class AudioStatusModel(BaseModel):
    provider: ProviderStatusModel
    #: Absent whenever the host mixer is not running — which is the common case,
    #: and not an error.
    host: HostMixerModel | None = None
    hostError: str | None = None


class MixerStateModel(BaseModel):
    """The dashboard's own routing matrix.

    Deliberately loose: the axes are discovered at runtime (strips come from
    whichever modules are loaded), so pinning a strip schema here would mean
    every new audio source needs a backend change. The frontend owns the shape;
    this endpoint owns durability. `version` is the guard that lets a future
    build refuse a document it cannot read.
    """

    version: int
    buses: list[dict[str, Any]] = Field(default_factory=list)
    strips: list[dict[str, Any]] = Field(default_factory=list)
    inputDeviceId: str = ""
    inputDeviceLabel: str = ""


class SetSendRequest(BaseModel):
    strip: int
    bus: str
    enabled: bool


class SetGainRequest(BaseModel):
    #: "strip" | "bus" — which axis `index` refers to.
    target: str
    index: int
    gainDb: float | None = None
    muted: bool | None = None


class CreateDeviceRequest(BaseModel):
    label: str = "Horrible Dashboard"


class LaunchRequest(BaseModel):
    #: 1 = Voicemeeter, 2 = Banana, 3 = Potato. Omitted means "whichever is
    #: installed", which is nearly always what you want — launching Banana on a
    #: machine set up around Potato presents a mixer whose saved routing is not
    #: the user's.
    kindId: int | None = None
