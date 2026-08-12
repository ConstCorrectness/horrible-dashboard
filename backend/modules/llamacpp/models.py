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
    #: One of `binaries.VARIANTS`. `cpu` on purpose: it is the only build
    #: guaranteed to run on the machine that downloaded it.
    variant: str = "cpu"


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
    gpuLayers: int = 0
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
