"""API models for the hardware probe.

Every field the probe measures is mirrored here on purpose: a `response_model`
filters anything it does not declare, silently, so a field added to
`probe.Profile` and forgotten here reaches the browser as `undefined` with no
error on either side.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AcceleratorModel(BaseModel):
    kind: str
    name: str
    vramMb: int | None = None
    unified: bool = False
    exact: bool = False
    detectedBy: str


class ProbeNoteModel(BaseModel):
    kind: str
    reason: str


class ProfileModel(BaseModel):
    os: str
    arch: str
    cpuCount: int
    ramMb: int | None = None
    ramExact: bool = False
    accelerators: list[AcceleratorModel] = Field(default_factory=list)
    notes: list[ProbeNoteModel] = Field(default_factory=list)
    probedAt: float
    overridden: bool = False
    #: False when "no accelerator" is a gap rather than a finding. The UI must
    #: say "unknown" here, never "none".
    certain: bool = True
    primary: AcceleratorModel | None = None


class DefaultsModel(BaseModel):
    llamaVariant: str
    gpuLayers: int
    threads: int
    traceTokenCap: int
    localTraining: bool
    #: Why each of the above was chosen, keyed by the field name. Shown in the
    #: pane so a CPU default on a GPU machine is explicable rather than mysterious.
    reasons: dict[str, str] = Field(default_factory=dict)


class HardwareModel(BaseModel):
    profile: ProfileModel
    defaults: DefaultsModel
