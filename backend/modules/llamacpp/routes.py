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
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from backend.modules.hardware import probe as hardware
from backend.modules.llamacpp import binaries, catalog, trace_runner, traces
from backend.modules.llamacpp.models import (
    DeleteModelRequest,
    DownloadRequest,
    EstimateRequest,
    EstimateResponse,
    InstallRequest,
    ModelEntry,
    ModelsResponse,
    RecordValues,
    RemoveInstallRequest,
    RepoFilesResponse,
    SpawnRequest,
    StatusResponse,
    TraceDetail,
    TraceListResponse,
    TraceRequest,
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
    variant = req.variant
    if variant in ("", "auto"):
        variant = hardware.defaults().llama_variant

    async def gen() -> AsyncIterator[str]:
        async for event in binaries.install_server(req.tag, variant):
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
    # Only when the caller said nothing: an explicit 0 is a request for pure CPU
    # and must survive, which is why these are `is None` checks and not falsiness.
    tuning = hardware.defaults()
    try:
        llama_manager.spawn(
            req.modelPath,
            alias=req.alias,
            port=req.port,
            context_size=req.contextSize,
            gpu_layers=tuning.gpu_layers if req.gpuLayers is None else req.gpuLayers,
            threads=req.threads if req.threads is not None else tuning.threads,
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


# ── traces ──────────────────────────────────────────────────────────────────
#
# The activations half of the module. A trace is produced by a subprocess and
# read back off disk, so these routes are a listing, a streamed run, and a
# reader — the pane never talks to the tracer directly.

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

#: A record's values are decoded server-side and capped: a residual activation
#: is tens of thousands of floats and no pane renders them all at once.
_VALUE_CAP = 8192


def _dims(model_path: str) -> dict[str, int]:
    """Layer count, width and head count, read from the GGUF header.

    Straight from the file rather than from a name or a guess — the same reason
    the model explorer exists. A model whose header omits them estimates as
    zero, which shows up as an obviously-wrong estimate instead of a
    confidently-wrong one.
    """
    from backend.modules.interpretability import gguf

    header = gguf.read_header(Path(model_path).expanduser())
    meta = header.metadata
    arch = str(meta.get("general.architecture") or "")

    def value(suffix: str) -> int:
        raw = meta.get(f"{arch}.{suffix}") if arch else None
        return int(raw) if isinstance(raw, int) else 0

    return {
        "layers": value("block_count"),
        "embeddingLength": value("embedding_length"),
        "heads": value("attention.head_count"),
    }


@router.get("/traces", response_model=TraceListResponse)
def list_traces() -> TraceListResponse:
    available, reason = trace_runner.available()
    return TraceListResponse(
        traces=[t.summary_dict() for t in traces.list_traces()],
        available=available,
        reason=reason,
        **traces.usage(),
    )


@router.post("/traces/estimate", response_model=EstimateResponse)
def estimate_trace(req: EstimateRequest) -> EstimateResponse:
    """What the run will cost, before it starts.

    A progress bar that has already started is too late: the difference between
    a 200 MB trace and a 12 GB one is the attention checkbox.
    """
    try:
        dims = _dims(req.modelPath)
    except Exception as exc:  # noqa: BLE001 — an unreadable header is an answer
        return EstimateResponse(error=str(exc))
    if not dims["layers"] or not dims["embeddingLength"]:
        # Every term in the estimate is proportional to these, so a header that
        # doesn't declare them yields zero bytes — which reads as "free" rather
        # than "unknown". Say which it is instead. (Whisper and TTS GGUFs in a
        # normal catalog do exactly this.)
        return EstimateResponse(
            **dims,
            budgetBytes=traces.budget_bytes(),
            error=(
                "this GGUF's header declares no block count or embedding length, "
                "so the cost of a trace can't be estimated from it — the run will "
                "still report what it wrote."
            ),
        )
    # Tokens are approximated by whitespace-ish splitting rather than by loading
    # the model to tokenize: this is an estimate, and loading a 20 GB GGUF to
    # refine an estimate would cost more than the trace.
    prompt_tokens = max(1, len(req.prompt.split()) * 4 // 3)
    result = traces.estimate(
        n_layer=dims["layers"],
        n_embd=dims["embeddingLength"],
        n_head=dims["heads"],
        prompt_tokens=prompt_tokens,
        gen_tokens=req.maxTokens,
        layers=len(req.layers) or None,
        attention=req.attention,
        fidelity=req.fidelity,
    )
    return EstimateResponse(
        **result.to_dict(),
        **dims,
        promptTokens=prompt_tokens,
        budgetBytes=traces.budget_bytes(),
    )


@router.post("/traces")
async def create_trace(req: TraceRequest) -> StreamingResponse:
    if req.fidelity not in traces.FIDELITIES:
        raise HTTPException(status_code=422, detail=f"unknown fidelity {req.fidelity}")

    async def gen() -> AsyncIterator[str]:
        spec = {
            "modelPath": req.modelPath,
            "prompt": req.prompt,
            "maxTokens": req.maxTokens,
            "layers": req.layers,
            "attention": req.attention,
            "fidelity": req.fidelity,
            "tokenCap": (
                hardware.defaults().trace_token_cap
                if req.tokenCap is None
                else req.tokenCap
            ),
            "gpuLayers": req.gpuLayers,
        }
        async for event in trace_runner.run_trace(spec):
            yield json.dumps(event) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


def _require(trace_id: str) -> traces.Trace:
    try:
        trace = traces.load(trace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if trace is None:
        raise HTTPException(status_code=404, detail=f"no trace {trace_id}")
    return trace


@router.get("/traces/{trace_id}", response_model=TraceDetail)
def get_trace(trace_id: str) -> TraceDetail:
    trace = _require(trace_id)
    tokens_path = trace.directory / "tokens.json"
    tokens: list[dict[str, Any]] = []
    if tokens_path.is_file():
        try:
            tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
        except ValueError:
            tokens = []
    return TraceDetail(
        trace=trace.summary_dict(),
        records=trace.manifest.get("records") or [],
        tokens=tokens,
    )


@router.get("/traces/{trace_id}/record/{index}", response_model=RecordValues)
def get_record(trace_id: str, index: int, limit: int = _VALUE_CAP) -> RecordValues:
    """One record's numbers.

    A `summary` record has no bytes by construction, so it comes back with its
    statistics and an empty `values` — never with zeros standing in for a tensor
    that was not stored.
    """
    trace = _require(trace_id)
    records = trace.records
    if not 0 <= index < len(records):
        raise HTTPException(status_code=404, detail=f"no record {index}")
    record = records[index]
    if record.fidelity == "summary" or record.length == 0:
        return RecordValues(record=record.to_dict(), summary=record.summary)
    count = max(1, min(limit, _VALUE_CAP))
    width = 2 if record.dtype in ("f16", "F16") else 4
    wanted = min(record.length, count * width)
    with trace.blob.open("rb") as handle:
        handle.seek(record.offset)
        payload = handle.read(wanted)
    values = traces.decode(payload, record.dtype)
    return RecordValues(
        record=record.to_dict(),
        values=values,
        truncated=wanted < record.length,
        summary=traces.summarize(values),
    )


@router.get("/traces/{trace_id}/tensors")
def get_tensors(trace_id: str, request: Request) -> Response:
    """The raw blob, honouring `Range`.

    One blob and byte ranges rather than a file per layer: per-layer files give
    thousands of handles and still cannot address a single node inside a layer.
    The range handling is karaoke's, including the suffix form — `bytes=-3` is
    the *last* three bytes, and reading it as a start offset is the classic
    misread that serves the wrong part of the file.
    """
    trace = _require(trace_id)
    path = trace.blob
    if not path.is_file():
        raise HTTPException(status_code=404, detail="this trace has no tensor blob")

    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(
            path,
            media_type="application/octet-stream",
            headers={"accept-ranges": "bytes"},
        )

    size = path.stat().st_size
    match = _RANGE_RE.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="malformed Range header")
    raw_start, raw_end = match.groups()
    if raw_start:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
    else:
        if not raw_end:
            raise HTTPException(status_code=416, detail="malformed Range header")
        start = max(0, size - int(raw_end))
        end = size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={"content-range": f"bytes */{size}"})

    def iter_range() -> Iterator[bytes]:
        remaining = end - start + 1
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(1 << 20, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        iter_range(),
        status_code=206,
        media_type="application/octet-stream",
        headers={
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(end - start + 1),
            "accept-ranges": "bytes",
        },
    )


@router.delete("/traces/{trace_id}")
def delete_trace(trace_id: str) -> dict[str, bool]:
    try:
        return {"deleted": traces.delete_trace(trace_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
