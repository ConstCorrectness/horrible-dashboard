"""HTTP surface for the llama.cpp provider: binary, weights, process.

Progress-bearing operations (installing a build, downloading a GGUF) stream NDJSON
rather than posting to a `/ws` channel. That is the shape `/agent/pull` already
uses and the pane already knows how to read, and it keeps a multi-gigabyte transfer
scoped to the request that asked for it — a client that navigates away stops the
stream instead of leaving a broadcast nobody is listening to.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from backend.modules.hardware import probe as hardware
from backend.modules.llamacpp import (
    binaries,
    catalog,
    findings,
    lens as lens_module,
    offload,
    trace_catalog,
    trace_runner,
    traces,
)
from backend.modules.llamacpp.models import (
    DeleteModelRequest,
    DownloadRequest,
    CaptureSet,
    CaptureSetsResponse,
    EstimateRequest,
    EstimateResponse,
    ForkRequest,
    InstallRequest,
    LayerPlanResponse,
    LensGridResponse,
    LensListResponse,
    LensSpecModel,
    LensTrackResponse,
    ModelEntry,
    ModelsResponse,
    RecordValues,
    RemoveInstallRequest,
    RepoFilesResponse,
    SaveFindingRequest,
    SaveFindingResponse,
    SeriesPoint,
    SpawnRequest,
    StatusResponse,
    TraceCatalogResponse,
    TraceSeriesResponse,
    TraceDetail,
    TraceListResponse,
    TraceRequest,
    VariantAvailabilityResponse,
    VocabEntry,
    VocabResponse,
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


@router.get("/install/variants", response_model=VariantAvailabilityResponse)
async def install_variants(tag: str = "latest") -> VariantAvailabilityResponse:
    """Which variants this release actually has a build for, on this OS/arch.

    Called at pane-load time so the picker can grey out a variant before the
    user picks it, instead of only discovering it doesn't exist after a click
    on Install (`select_asset` inside `install_server`).
    """
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            info = await binaries.variant_availability(client, tag)
        except httpx.HTTPError as exc:
            os_token, arch = binaries.platform_tokens()
            return VariantAvailabilityResponse(
                tag=tag,
                os=os_token,
                arch=arch,
                variants={},
                error=f"could not reach the llama.cpp releases API: {exc}",
            )
    return VariantAvailabilityResponse(**info)


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


@router.get("/models/layers", response_model=LayerPlanResponse)
async def model_layers(path: str) -> LayerPlanResponse:
    """Per-layer byte sizes for one GGUF, so the caller can show what fits in VRAM.

    Restricted to files the **catalog already knows about**: this opens and reads a
    path the client supplied, and a route that reads an arbitrary path is an
    arbitrary-file-read route. `find_model` resolves against the managed directory,
    the extra dirs, and the Ollama/LM Studio stores — the same set the model picker
    offers — so anything reachable here was already listed.
    """
    if catalog.find_model(path) is None:
        raise HTTPException(status_code=404, detail="not a model in the catalog")
    plan = await asyncio.to_thread(offload.layer_plan, path)
    return LayerPlanResponse(**plan)


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


def _architecture(model_path: str) -> str:
    """`general.architecture` from the GGUF header, or "" if it can't be read.

    Decides the tracer's default capture set (`tracer.capture_for`), so a Mamba or
    RWKV model records its own mechanism rather than only the residual stream.
    Failing soft is right here: an unreadable header means the transformer defaults,
    which is what every previous trace used and is never worse than today.
    """
    from backend.modules.interpretability import gguf

    try:
        header = gguf.read_header(Path(model_path).expanduser())
    except (OSError, gguf.GgufError, ValueError) as exc:
        logger.info("llamacpp: no architecture for %s (%s)", model_path, exc)
        return ""
    return str(header.metadata.get("general.architecture") or "")


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


@router.get("/traces/capture-sets", response_model=CaptureSetsResponse)
def capture_sets() -> CaptureSetsResponse:
    """The named capture sets, with their real ggml node patterns.

    Served rather than restated in TypeScript, for the reason `plane_order` is:
    two lists of ggml node names in two languages is one upstream rename away
    from a capture set that matches nothing and fails silently.
    """
    from backend.modules.llamacpp.tracer import CAPTURE_PRESETS

    return CaptureSetsResponse(
        sets=[
            CaptureSet(
                id="default",
                label="Everything",
                patterns=list(CAPTURE_PRESETS["default"]),
                note="The architecture's own default set — six activations per block.",
            ),
            CaptureSet(
                id="lens",
                label="Lens only",
                patterns=list(CAPTURE_PRESETS["lens"]),
                note=(
                    "The residual stream and the output head. All the lens reads, "
                    "and a fraction of the bytes — which is what makes swapping a "
                    "token and looking again take seconds."
                ),
            ),
        ]
    )


@router.get("/traces/catalog", response_model=TraceCatalogResponse)
def trace_catalog_rows(
    limit: int = 50, modelSha: str = "", derivedFrom: str = ""
) -> TraceCatalogResponse:
    """The trace catalog in `app.db`, which is what makes traces joinable.

    Declared **above** `/traces/{trace_id}`, and that is load-bearing: FastAPI
    matches in declaration order, so a static path below the parameterized one
    is never reached — the id route answers first and 404s with "no trace
    catalog".

    Served alongside `GET /traces` rather than replacing it: that one walks the
    directory and is the authority, this one answers questions the directory
    cannot (every fork of a trace, every trace of a model hash) without parsing
    every manifest on disk.
    """
    return TraceCatalogResponse(
        traces=trace_catalog.rows(
            limit=limit, model_sha=modelSha, derived_from=derivedFrom
        )
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
    prompt_tokens = (
        len(req.tokenIds) if req.tokenIds else max(1, len(req.prompt.split()) * 4 // 3)
    )
    result = traces.estimate(
        n_layer=dims["layers"],
        n_embd=dims["embeddingLength"],
        n_head=dims["heads"],
        prompt_tokens=prompt_tokens,
        gen_tokens=req.maxTokens,
        layers=len(req.layers) or None,
        attention=req.attention,
        fidelity=req.fidelity,
        nodes_per_layer=traces.nodes_per_layer(req.capture),
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
            "tokenIds": req.tokenIds,
            "capture": req.capture,
            "derivedFrom": req.derivedFrom,
            "edits": [edit.model_dump() for edit in req.edits],
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
            # Read here rather than in the tracer subprocess: the header is already
            # opened on this side for the estimate, and the subprocess should be
            # handed a decided spec rather than re-deriving one.
            "architecture": await asyncio.to_thread(_architecture, req.modelPath),
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


def _trace_tokens(trace: traces.Trace) -> list[dict[str, Any]]:
    """The tokens a pass ran on. An unreadable file is an empty strip, not a 500."""
    tokens_path = trace.directory / "tokens.json"
    if not tokens_path.is_file():
        return []
    try:
        return list(json.loads(tokens_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


@router.get("/traces/{trace_id}", response_model=TraceDetail)
def get_trace(trace_id: str) -> TraceDetail:
    trace = _require(trace_id)
    return TraceDetail(
        trace=trace.summary_dict(),
        records=trace.manifest.get("records") or [],
        tokens=_trace_tokens(trace),
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


@router.get("/traces/{trace_id}/series", response_model=TraceSeriesResponse)
def get_series(trace_id: str, name: str, stat: str = "rms") -> TraceSeriesResponse:
    """One node's statistic per forward pass — the watch window's sparkline.

    Addressed by **node name**, not record index, because that is what a pin is: the
    same node in pass 3 is a different record, and asking for it by index would mean
    the client resolving the very thing it is asking about.

    Summarizes the **whole** record rather than the first `_VALUE_CAP` values the way
    `get_record` does. A series is a comparison across passes, and a statistic over a
    prefix compared against a statistic over a different prefix is not one. These are
    activations of a few tens of KB, so reading them whole is cheap; `get_record` caps
    because it ships the values themselves to a browser, which this does not.
    """
    trace = _require(trace_id)
    wanted = name.strip()
    if not wanted:
        raise HTTPException(status_code=422, detail="name is required")

    matches = [r for r in trace.records if r.name == wanted]
    if not matches:
        raise HTTPException(
            status_code=404, detail=f"no node named {wanted!r} in this trace"
        )

    points: list[SeriesPoint] = []
    with trace.blob.open("rb") as handle:
        for record in sorted(matches, key=lambda r: r.pass_index):
            value: float | None = None
            if record.fidelity == "summary" or record.length == 0:
                # A summary record has no bytes by construction. It may still carry
                # the statistic from when it was summarized; if it does not, the pass
                # is a gap rather than a zero.
                raw_stat = record.summary.get(stat)
                value = float(raw_stat) if isinstance(raw_stat, (int, float)) else None
            else:
                handle.seek(record.offset)
                values = traces.decode(handle.read(record.length), record.dtype)
                computed = traces.summarize(values).get(stat)
                value = float(computed) if computed is not None else None
            points.append(
                SeriesPoint(
                    passIndex=record.pass_index, value=value, fidelity=record.fidelity
                )
            )

    return TraceSeriesResponse(name=wanted, stat=stat, points=points)


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


@router.post("/traces/{trace_id}/fork")
async def fork_trace(trace_id: str, req: ForkRequest) -> StreamingResponse:
    """Re-run a trace with some of its tokens replaced.

    The counterfactual half of the lens: a grid tells you what the model was
    disposed to say, and a fork tells you what changing one word does to that.
    Everything but the tokens is inherited from the parent, so the two grids
    differ in exactly the place you edited — a fork that also changed the
    fidelity or the layer selection would not be comparable to what it forked.
    """
    parent = _require(trace_id)
    try:
        spec = fork_spec(parent, [edit.model_dump() for edit in req.edits])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def gen() -> AsyncIterator[str]:
        spec["architecture"] = await asyncio.to_thread(
            _architecture, str(spec["modelPath"])
        )
        async for event in trace_runner.run_trace(spec):
            yield json.dumps(event) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


def fork_spec(parent: traces.Trace, edits: list[dict[str, Any]]) -> dict[str, Any]:
    """The tracer spec for a fork of `parent`.

    Pure, and separated from the route for that reason: the subprocess it feeds
    needs a native library, and the thing that can actually be wrong here — which
    token ends up where — does not.
    """
    prompt_tokens = [t for t in _trace_tokens(parent) if not t.get("generated")]
    if not prompt_tokens:
        raise ValueError(
            "this trace records no prompt tokens, so there is nothing to fork"
        )
    tokens = [int(t.get("id", 0)) for t in prompt_tokens]

    stamped: list[dict[str, Any]] = []
    for edit in edits:
        position = int(edit.get("position", -1))
        if not 0 <= position < len(tokens):
            raise ValueError(
                f"position {position} is outside this trace's "
                f"{len(tokens)} prompt tokens"
            )
        # `fromId` is stamped from the parent rather than trusted from the
        # caller: it is the record of what was actually replaced, and a client
        # that sent a stale one would make the fork's own provenance wrong.
        stamped.append(
            {
                "position": position,
                "fromId": int(prompt_tokens[position].get("id", -1)),
                "toId": int(edit.get("toId", 0)),
            }
        )
        tokens[position] = int(edit.get("toId", 0))

    manifest = parent.manifest
    return {
        "modelPath": manifest.get("modelPath", ""),
        "prompt": manifest.get("prompt", ""),
        "tokenIds": tokens,
        "capture": list(manifest.get("capture") or []),
        "derivedFrom": parent.trace_id,
        "edits": stamped,
        "maxTokens": int(manifest.get("maxTokens") or 0),
        "layers": list(manifest.get("layers") or []),
        "attention": bool(manifest.get("attention")),
        "fidelity": str(manifest.get("fidelity") or "fp16"),
        # The parent already ran within a cap; re-imposing the hardware probe's
        # current one would silently truncate a fork of a trace made on a better
        # machine, and a fork shorter than its parent is not a comparison.
        "tokenCap": traces.MAX_TRACE_TOKENS,
        "gpuLayers": 0,
    }


# ── the lens ────────────────────────────────────────────────────────────────
#
# A trace read as *words* rather than as numbers. Everything here is derived
# from records the trace already holds plus the model's own output head, so a
# lens costs no new forward pass — which is what makes swapping a token and
# re-reading the grid a seconds-long loop instead of a minutes-long one.


def _lens_ids(raw: str) -> list[int]:
    """A comma-separated `layers=` / `positions=` filter.

    Silently dropping an unparseable entry would answer a question the caller
    did not ask, so a malformed filter is a 400.
    """
    if not raw.strip():
        return []
    try:
        return [int(part) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"not a list of integers: {raw!r}"
        ) from exc


@router.get("/traces/{trace_id}/lens", response_model=LensGridResponse)
def get_lens_grid(
    trace_id: str,
    lens: str = "identity",
    k: int = 5,
    layers: str = "",
    positions: str = "",
    passIndex: int = 0,
) -> LensGridResponse:
    """The layer x position grid for one traced pass."""
    trace = _require(trace_id)
    try:
        grid = lens_module.compute_grid(
            trace,
            lens_id=lens,
            k=max(1, min(k, 100)),
            layers=_lens_ids(layers),
            positions=_lens_ids(positions),
            pass_index=passIndex,
        )
    except lens_module.LensError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    data = grid.to_dict()
    data["tokens"] = _trace_tokens(trace)
    return LensGridResponse(**data)


@router.get("/traces/{trace_id}/lens/track", response_model=LensTrackResponse)
def get_lens_track(
    trace_id: str, tokenId: int, lens: str = "identity", passIndex: int = 0
) -> LensTrackResponse:
    """One vocabulary token's rank and logit at every cell — the token pin."""
    trace = _require(trace_id)
    try:
        tracked = lens_module.track_token(
            trace, tokenId, lens_id=lens, pass_index=passIndex
        )
    except lens_module.LensError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LensTrackResponse(**tracked)


@router.get("/traces/{trace_id}/lenses", response_model=LensListResponse)
def list_lenses(trace_id: str) -> LensListResponse:
    """Which lenses apply to this trace's model.

    Keyed by the trace rather than by a model path so the id in the URL is the
    same one every other lens route takes, and so a fitted lens can never be
    offered for weights it was not fitted on.
    """
    trace = _require(trace_id)
    model_sha = str(trace.manifest.get("modelSha") or "")
    specs = [
        LensSpecModel(**s.to_dict()) for s in lens_module.available_lenses(model_sha)
    ]
    try:
        lens_module.load_unembedding(str(trace.manifest.get("modelPath") or ""))
    except lens_module.LensError as exc:
        return LensListResponse(lenses=specs, available=False, reason=str(exc))
    return LensListResponse(lenses=specs, available=True)


@router.get("/models/vocab", response_model=VocabResponse)
def get_vocab(path: str, q: str = "", limit: int = 50) -> VocabResponse:
    """Search a GGUF's own vocabulary — the token picker behind a swap.

    The model's vocabulary and not an HF tokenizer's: a swap has to name a token
    the traced weights actually have, and `tokenizer.py`'s family fallback can
    hand back the wrong generation's vocabulary while looking precise.
    """
    try:
        un = lens_module.load_unembedding(path)
    except lens_module.LensError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    needle = q.strip()
    cap = max(1, min(limit, 500))
    matches: list[VocabEntry] = []
    truncated = False
    for token_id, piece in enumerate(un.vocab):
        text = lens_module.render_piece(piece, un.tokenizer_model)
        if needle and needle not in text and needle not in piece:
            continue
        if len(matches) >= cap:
            truncated = True
            break
        matches.append(VocabEntry(id=token_id, piece=piece, text=text))
    return VocabResponse(
        tokens=matches,
        total=len(un.vocab),
        tokenizerModel=un.tokenizer_model,
        truncated=truncated,
    )


@router.post("/traces/{trace_id}/finding", response_model=SaveFindingResponse)
async def save_finding(trace_id: str, req: SaveFindingRequest) -> SaveFindingResponse:
    """Write this reading into the library, so it outlives the trace."""
    result = await findings.save_finding(
        trace_id,
        note=req.note,
        library=req.library,
        lens_id=req.lens,
        k=req.k,
        layers=req.layers,
        positions=req.positions,
        pass_index=req.passIndex,
    )
    # A refusal is a 200 with `error` set, not a 4xx: "this grid is unverified" is
    # an answer about the reading, and the pane renders it beside the verify chip
    # that already says so rather than as a failed request.
    return SaveFindingResponse(**result)


@router.delete("/traces/{trace_id}")
def delete_trace(trace_id: str) -> dict[str, bool]:
    try:
        return {"deleted": traces.delete_trace(trace_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
