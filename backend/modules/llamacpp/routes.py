"""HTTP surface for the llama.cpp provider: binary, weights, process.

Progress-bearing operations (installing a build, downloading a GGUF) stream NDJSON
rather than posting to a `/ws` channel. That is the shape `/agent/pull` already
uses and the pane already knows how to read, and it keeps a multi-gigabyte transfer
scoped to the request that asked for it — a client that navigates away stops the
stream instead of leaving a broadcast nobody is listening to.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator


from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.modules.llamacpp import binaries, catalog
from backend.modules.llamacpp.models import (
    DeleteModelRequest,
    DownloadRequest,
    InstallRequest,
    ModelEntry,
    ModelsResponse,
    RemoveInstallRequest,
    RepoFilesResponse,
    SpawnRequest,
    StatusResponse,
)
from backend.modules.llamacpp.server import llama_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llamacpp", tags=["llamacpp"])


def _is_agent_provider() -> bool:
    """Whether the agent's configured provider is this one.

    Read through the agent module's own config loader rather than duplicating the
    file format — the pane's "this is what your agent is talking to" badge must not
    be able to disagree with what the orchestrator actually does.
    """
    try:
        from backend.modules.agent.routes import _load_config

        config = _load_config()
        return bool(config and config.provider == "llamacpp")
    except Exception as exc:  # noqa: BLE001 — a status poll must never 500
        logger.info("llamacpp: could not read the agent config (%s)", exc)
        return False


@router.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    return StatusResponse(
        **llama_manager.status(), isAgentProvider=_is_agent_provider()
    )


@router.post("/install")
async def install(req: InstallRequest) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        async for event in binaries.install_server(req.tag, req.variant):
            yield json.dumps(event) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.post("/install/remove")
def remove_install(req: RemoveInstallRequest) -> dict[str, bool]:
    if llama_manager.running():
        raise HTTPException(
            status_code=409, detail="stop the running server before removing its build"
        )
    return {"removed": binaries.remove_install(req.tag, req.variant)}


@router.get("/models", response_model=ModelsResponse)
def models() -> ModelsResponse:
    entries = [ModelEntry(**m.to_dict()) for m in catalog.list_models()]
    return ModelsResponse(
        models=entries, suggested=list(catalog.suggested_repos()), **catalog.usage()
    )


@router.get("/repo", response_model=RepoFilesResponse)
async def repo_files(repo: str) -> RepoFilesResponse:
    """The GGUF files in a Hugging Face repo, so the caller can pick a quantization."""
    if not repo.strip():
        raise HTTPException(status_code=422, detail="repo is required")
    try:
        files = await catalog.list_repo_ggufs(repo.strip())
    except Exception as exc:  # noqa: BLE001 — network/JSON shapes vary; surface it
        return RepoFilesResponse(repo=repo, error=str(exc))
    return RepoFilesResponse(repo=repo, files=files)


@router.post("/models/download")
async def download(req: DownloadRequest) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        async for event in catalog.download_model(req.repo, req.file):
            yield json.dumps(event) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.post("/models/delete")
def delete_model(req: DeleteModelRequest) -> dict[str, bool]:
    if llama_manager.model_path == req.path:
        raise HTTPException(
            status_code=409, detail="that model is loaded — stop the server first"
        )
    try:
        catalog.delete_model(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.post("/start", response_model=StatusResponse)
async def start(req: SpawnRequest) -> StatusResponse:
    try:
        llama_manager.spawn(
            req.modelPath,
            alias=req.alias,
            port=req.port,
            context_size=req.contextSize,
            gpu_layers=req.gpuLayers,
            threads=req.threads,
            extra_args=req.extraArgs,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if req.wait:
        await llama_manager.wait_ready()
    return StatusResponse(
        **llama_manager.status(), isAgentProvider=_is_agent_provider()
    )


@router.post("/stop", response_model=StatusResponse)
def stop() -> StatusResponse:
    return StatusResponse(**llama_manager.stop(), isAgentProvider=_is_agent_provider())
