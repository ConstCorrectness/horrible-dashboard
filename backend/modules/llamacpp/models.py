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


class RepoFilesResponse(BaseModel):
    repo: str
    files: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""


class DownloadRequest(BaseModel):
    repo: str
    file: str


class DeleteModelRequest(BaseModel):
    path: str


class TraceRequest(BaseModel):
    """Run one traced forward pass.

    `prompt` is traced as **raw text** — no chat template is applied, so what is
    tokenized is exactly what is shown. See `tracer.py`.
    """

    modelPath: str
    prompt: str
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
