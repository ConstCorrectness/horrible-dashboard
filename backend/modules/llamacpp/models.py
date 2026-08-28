"""Request/response models for `/api/llamacpp`.

Response shapes are camelCase because the pane consumes them directly, matching
the interpretability module next door.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InstallRequest(BaseModel):
    #: A release tag (`b4567`) or "latest".
    tag: str = "latest"
    #: One of `binaries.VARIANTS`, or `auto` to take the hardware probe's answer.
    #: `auto` is the default because the previous one — a flat `cpu` — was right
    #: on the machine with no GPU and silently wrong on every machine with one.
    #: The probe still falls back to `cpu` whenever it could not determine what
    #: the machine has: a CUDA build that cannot load its runtime looks exactly
    #: like a broken install.
    variant: str = "auto"


class RemoveInstallRequest(BaseModel):
    tag: str
    variant: str = "cpu"


class VariantAvailabilityResponse(BaseModel):
    tag: str
    os: str
    arch: str
    #: variant -> whether this release publishes a build for it on this OS/arch.
    variants: dict[str, bool]
    error: str = ""


class SpawnRequest(BaseModel):
    """Start a server on a GGUF the catalog knows about.

    `modelPath` and not a model *name*: the whole point of this provider is that
    the node picks the file, and a name would have to be resolved back to one
    through exactly the guessing the catalog exists to remove.
    """

    modelPath: str
    alias: str = ""
    port: int | None = None
    contextSize: int = 4096
    #: Layers offloaded to the GPU. 0 = pure CPU, which is what the `cpu` build
    #: can do; raising it on a cpu-only build is silently ignored by llama-server.
    #: `None` means "ask the hardware probe" — an explicit 0 still means 0, which
    #: is why this is nullable rather than defaulting to a sentinel integer.
    gpuLayers: int | None = None
    threads: int | None = None
    extraArgs: list[str] = Field(default_factory=list)
    #: Wait for `/health` before returning, so the caller knows the difference
    #: between "spawned" and "can answer a request".
    wait: bool = True


class ModelEntry(BaseModel):
    path: str
    origin: str
    name: str
    sizeBytes: int
    architecture: str = ""
    parameters: int | None = None
    contextLength: int | None = None
    quantization: str = ""
    error: str = ""
    deletable: bool = False


class ModelsResponse(BaseModel):
    models: list[ModelEntry] = Field(default_factory=list)
    usedBytes: int = 0
    budgetBytes: int = 0
    root: str = ""
    extraDirs: list[str] = Field(default_factory=list)
    suggested: list[dict[str, str]] = Field(default_factory=list)


class LayerPlanResponse(BaseModel):
    """Where one GGUF's bytes sit, block by block.

    `layerBytes` is indexed by transformer block; `overheadBytes` is everything
    outside the stack (embeddings, final norm, output head), kept separate because
    `--n-gpu-layers` moves the blocks and only reaches the output tensors when the
    count exceeds the block count.
    """

    path: str = ""
    layerCount: int = 0
    layerBytes: list[int] = Field(default_factory=list)
    overheadBytes: int = 0
    totalBytes: int = 0
    #: KV cache cost of a *single token* across all layers, so the caller can
    #: multiply by a context size it is still letting the user change. None when
    #: the metadata lacks a head count — an invented cache size would sit next to
    #: measured ones and look equally solid.
    kvBytesPerToken: int | None = None
    contextLength: int | None = None
    #: False when a tensor used an unrecognized quantization, making every total a
    #: floor rather than an answer.
    complete: bool = True
    error: str = ""


class SeriesPoint(BaseModel):
    passIndex: int
    #: None when that pass has the node but no values to summarize — a `summary`
    #: record carrying no stored statistic. Drawn as a gap, never interpolated:
    #: a straight line through a pass we did not measure is a fabricated reading.
    value: float | None = None
    fidelity: str = ""


class TraceSeriesResponse(BaseModel):
    """One node's statistic across every forward pass of a trace — the watch
    window's sparkline, and the only view here that is *about* generation rather
    than about a single pass."""

    name: str = ""
    stat: str = "rms"
    points: list[SeriesPoint] = Field(default_factory=list)
    error: str = ""


class ProfilePoint(BaseModel):
    """One record of one pass, with one statistic computed over its whole tensor."""

    index: int
    name: str = ""
    layer: int | None = None
    #: None when the record has no values to summarize and carried no stored
    #: statistic either. A gap, never a zero: zero is a measurement, and plotting
    #: it would draw a reading that was never taken.
    value: float | None = None
    fidelity: str = ""


class TraceProfileResponse(BaseModel):
    """Every record of one forward pass, reduced to one statistic.

    The pane arranges these into a role-against-depth profile and a kind-by-layer
    fingerprint. It cannot compute them itself: `tracer._capture` writes `summary`
    only for `summary`-fidelity records, so exactly the records that hold data
    carry no statistics in the manifest.
    """

    passIndex: int = 0
    stat: str = "rms"
    points: list[ProfilePoint] = Field(default_factory=list)
    error: str = ""


class RepoFilesResponse(BaseModel):
    repo: str
    files: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""


class DownloadRequest(BaseModel):
    repo: str
    file: str


class DeleteModelRequest(BaseModel):
    path: str


class TokenEdit(BaseModel):
    """One token swapped out of a parent trace's sequence."""

    position: int
    fromId: int = -1
    toId: int


class TraceRequest(BaseModel):
    """Run one traced forward pass.

    `prompt` is traced as **raw text** — no chat template is applied, so what is
    tokenized is exactly what is shown. See `tracer.py`.
    """

    modelPath: str
    prompt: str = ""
    #: The exact tokens to run, bypassing tokenization. This is what makes a
    #: swapped trace possible at all: re-tokenizing an edited *string* can change
    #: neighbouring tokens too, so the counterfactual would differ in more than
    #: the one place you changed. When set, `prompt` is descriptive only.
    tokenIds: list[int] | None = None
    #: Graph-node name patterns to capture. Empty = the architecture's default
    #: set. A lens needs only the residual stream, which is ~1% of a full trace.
    capture: list[str] = Field(default_factory=list)
    #: The trace this one was forked from, and what was changed. Provenance, not
    #: behaviour — a fork runs exactly as an ordinary trace does.
    derivedFrom: str = ""
    edits: list[TokenEdit] = Field(default_factory=list)
    #: Tokens to generate after the prompt. Each is its own forward pass and its
    #: own set of records; 0 traces the prompt alone.
    maxTokens: int = 0
    #: Decoder blocks to capture. Empty = all of them, which is the expensive
    #: default and the reason the estimate exists.
    layers: list[int] = Field(default_factory=list)
    #: Attention scores. Off by default: the score matrix is the single largest
    #: thing in a trace and grows with the square of the token count.
    attention: bool = False
    fidelity: str = "fp16"
    #: `None` = take the hardware probe's cap, which is set from RAM (the tracer
    #: runs on the CPU wheel, so RAM and not VRAM is the binding constraint).
    tokenCap: int | None = None
    gpuLayers: int = 0


class EstimateRequest(BaseModel):
    modelPath: str
    prompt: str = ""
    maxTokens: int = 0
    layers: list[int] = Field(default_factory=list)
    attention: bool = False
    fidelity: str = "fp16"
    capture: list[str] = Field(default_factory=list)
    tokenIds: list[int] | None = None


class CaptureSet(BaseModel):
    id: str
    label: str
    #: The real ggml node-name substrings. Empty means "the architecture's own
    #: default", which the tracer chooses — a client must not guess at it.
    patterns: list[str] = Field(default_factory=list)
    note: str = ""


class CaptureSetsResponse(BaseModel):
    sets: list[CaptureSet] = Field(default_factory=list)


class ForkRequest(BaseModel):
    """Re-run a trace with some of its tokens replaced.

    Everything else — model, generation length, layer selection, fidelity — is
    inherited from the parent, because a counterfactual that also changed the
    capture settings is not a counterfactual.
    """

    edits: list[TokenEdit] = Field(default_factory=list)


class EstimateResponse(BaseModel):
    bytes: int = 0
    seconds: float = 0.0
    note: str = ""
    #: Header facts the estimate was computed from, so a wrong-looking number
    #: can be traced to a wrong-looking model rather than to arithmetic.
    layers: int = 0
    embeddingLength: int = 0
    heads: int = 0
    promptTokens: int = 0
    budgetBytes: int = 0
    error: str = ""


class TraceListResponse(BaseModel):
    traces: list[dict[str, Any]] = Field(default_factory=list)
    usedBytes: int = 0
    budgetBytes: int = 0
    root: str = ""
    #: False when `llama-cpp-python` is missing, with `reason` saying so. The
    #: pane shows the install line instead of an empty list that looks broken.
    available: bool = False
    reason: str = ""


class TraceDetail(BaseModel):
    trace: dict[str, Any] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list)
    tokens: list[dict[str, Any]] = Field(default_factory=list)


class RecordValues(BaseModel):
    """One record's numbers, decoded server-side.

    Decoding fp16 in TypeScript is possible and pointless: the backend already
    knows the dtype and the byte order it wrote, and shipping that knowledge to
    the client is a second implementation waiting to disagree.
    """

    record: dict[str, Any] = Field(default_factory=dict)
    values: list[float] = Field(default_factory=list)
    #: True when `values` is a prefix of the tensor rather than all of it.
    truncated: bool = False
    summary: dict[str, float] = Field(default_factory=dict)


class LensSpecModel(BaseModel):
    """A transport a lens can apply before unembedding."""

    id: str = ""
    kind: str = "identity"
    label: str = ""
    provenance: str = ""
    layers: list[int] = Field(default_factory=list)
    dModel: int = 0


class LensListResponse(BaseModel):
    lenses: list[LensSpecModel] = Field(default_factory=list)
    #: False when the model's output head cannot be read at all, with `reason`
    #: naming the quantization. The pane says so rather than showing an empty grid.
    available: bool = True
    reason: str = ""


class LensGridResponse(BaseModel):
    """The layer x position readout.

    `verified` is deliberately three-valued. `true` means the identity lens
    reproduced this trace's own captured logits; `false` means it did not and
    every cell is suspect; `unavailable` means there was nothing to check
    against. Rendering the third as the first is the failure this whole surface
    is arranged to prevent.
    """

    layers: list[int] = Field(default_factory=list)
    positions: list[int] = Field(default_factory=list)
    #: None where llama.cpp did not compute that position at that layer —
    #: a blank cell, never a fabricated one.
    cells: list[list[dict[str, Any] | None]] = Field(default_factory=list)
    lens: LensSpecModel = Field(default_factory=LensSpecModel)
    unembedding: dict[str, Any] = Field(default_factory=dict)
    tokens: list[dict[str, Any]] = Field(default_factory=list)
    verified: str = "unavailable"
    verifyNote: str = ""
    verifyDetail: dict[str, Any] = Field(default_factory=dict)


class LensTrackResponse(BaseModel):
    """One vocabulary token's logit and rank at every cell."""

    tokenId: int = 0
    text: str = ""
    layers: list[int] = Field(default_factory=list)
    positions: list[int] = Field(default_factory=list)
    logits: list[list[float | None]] = Field(default_factory=list)
    ranks: list[list[int | None]] = Field(default_factory=list)
    lens: LensSpecModel = Field(default_factory=LensSpecModel)


class VocabEntry(BaseModel):
    id: int
    #: The raw GGUF vocabulary entry ("Ġthe"), kept beside the rendered text so
    #: a search that matches the encoding still finds its token.
    piece: str = ""
    text: str = ""


class VocabResponse(BaseModel):
    tokens: list[VocabEntry] = Field(default_factory=list)
    total: int = 0
    tokenizerModel: str = ""
    truncated: bool = False


class SaveFindingRequest(BaseModel):
    """File a lens reading into the knowledge library as a `note` source.

    The grid is recomputed here rather than posted from the pane: the browser
    holds a *rendered* grid (already narrowed to what fit on screen, already
    rounded for display), and a note written from that would record the picture
    instead of the reading.
    """

    note: str = ""
    library: str = "default"
    lens: str = "identity"
    k: int = 5
    layers: list[int] = Field(default_factory=list)
    positions: list[int] = Field(default_factory=list)
    passIndex: int = 0


class SaveFindingResponse(BaseModel):
    sourceId: str = ""
    library: str = ""
    title: str = ""
    traceId: str = ""
    chars: int = 0
    chunks: int = 0
    #: Set when the reading was refused (an unverified grid is not a finding) or
    #: when the note was created but could not be indexed — a source with zero
    #: chunks is one no search will ever return, so it is not a save.
    error: str = ""
    verified: str = ""
    verifyNote: str = ""


class TraceCatalogResponse(BaseModel):
    """The `llamacpp_traces` rows. The catalog, not the directory — see
    `trace_catalog.py` for why both exist."""

    traces: list[dict[str, Any]] = Field(default_factory=list)


class StatusResponse(BaseModel):
    """Everything the pane needs in one poll: binary, process, weights."""

    installed: bool = False
    install: dict[str, Any] | None = None
    installs: list[dict[str, Any]] = Field(default_factory=list)
    running: bool = False
    ready: bool = False
    modelPath: str | None = None
    model: str = ""
    endpoint: str = ""
    pid: int | None = None
    error: str = ""
    uptimeSeconds: float = 0.0
    logs: list[str] = Field(default_factory=list)
    #: True when the agent's active provider is this one — the pane says so, since
    #: a running server nothing is pointed at is a common and confusing state.
    isAgentProvider: bool = False
