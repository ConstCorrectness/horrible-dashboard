"""HTTP surface of the interpretability library and worker, at `/api/interp`."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.modules.interp import (
    artifacts,
    catalog,
    compat,
    env,
    graphs,
    lenses,
    neuronpedia,
    sae,
    steer,
)
from backend.modules.interp.worker import WorkerUnavailable, worker

router = APIRouter(prefix="/interp", tags=["interp"])

#: The worker ops the pane may call directly. Later tools call theirs from the
#: backend, with their own validation, rather than through this pass-through.
WORKER_OPS = {"ping", "status", "load", "unload", "generate"}


class DownloadRequest(BaseModel):
    entryId: str
    choice: str = ""
    #: The size the user was shown and confirmed. The download is refused if the
    #: Hub now reports a different size — consent was to *that* many bytes.
    confirmBytes: int


class InstallRequest(BaseModel):
    group: str = "base"


class LabelCheckRequest(BaseModel):
    artifactId: str
    index: int
    #: The trace whose model the example is traced through.
    traceId: str


class SteerRequest(BaseModel):
    artifactId: str
    feature: int
    #: In units of the feature's own peak (see `steer.py`): 1.0 = as strong as it
    #: ever fires on this prompt. Coherent text survives roughly -1.5..1.5.
    strength: float = 1.0
    #: The trace whose model (and, with no `prompt`, whose prompt) to steer. It is
    #: reused as the default run when it is the same experiment.
    traceId: str = ""
    #: A fresh prompt instead, on `traceId`'s model (or `modelPath`).
    prompt: str = ""
    modelPath: str = ""
    maxTokens: int = steer.DEFAULT_NEW_TOKENS


class GraphImportRequest(BaseModel):
    #: A Neuronpedia graph page URL or `model/slug`…
    ref: str = ""
    #: …or the graph file itself (a local circuit-tracer run).
    json_text: str = ""
    name: str = ""


class GraphQParams(BaseModel):
    pinnedIds: list[str] = []
    #: `[label, *node_ids]` groups.
    supernodes: list[list[str]] = []


class LensInstallRequest(BaseModel):
    artifactId: str
    #: The GGUF to install the lens for (the lens slot is keyed by its sha).
    modelPath: str


class WorkerOpRequest(BaseModel):
    op: str
    args: dict[str, Any] = {}


# ── library ──────────────────────────────────────────────────────────────────


@router.get("/library/catalog")
def get_catalog() -> dict[str, Any]:
    return {"entries": [asdict(e) for e in catalog.CATALOG]}


@router.get("/library/catalog/{entry_id}/choices")
async def get_choices(entry_id: str) -> dict[str, Any]:
    e = catalog.entry(entry_id)
    if e is None:
        raise HTTPException(status_code=404, detail=f"unknown entry {entry_id}")
    try:
        choices = await catalog.browse(e, await artifacts._token())
    except httpx.HTTPError as exc:
        return {"choices": [], "error": f"could not list {e.repo}: {exc}"}
    installed = {a["id"] for a in artifacts.list_artifacts()}
    for c in choices:
        c["installed"] = artifacts.artifact_id(entry_id, c["id"]) in installed
    return {"choices": choices}


@router.post("/library/download")
async def post_download(req: DownloadRequest) -> StreamingResponse:
    e = catalog.entry(req.entryId)
    if e is None:
        raise HTTPException(status_code=404, detail=f"unknown entry {req.entryId}")
    choices = await catalog.browse(e, await artifacts._token())
    picked = next((c for c in choices if c["id"] == req.choice), None)
    if picked is None:
        raise HTTPException(status_code=404, detail=f"{req.choice!r} is not a choice")
    if int(picked["bytes"]) != req.confirmBytes:
        raise HTTPException(
            status_code=409,
            detail=(
                f"the size changed since it was shown ({req.confirmBytes} → "
                f"{picked['bytes']} bytes); confirm the new size"
            ),
        )

    async def gen() -> AsyncIterator[str]:
        async for event in artifacts.download(req.entryId, req.choice):
            yield json.dumps(event) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


async def _targets() -> list[dict[str, Any]]:
    """The models an artifact can be checked against: the agent's GGUF, and the
    worker's loaded HF model when there is one."""
    from backend.modules.interpretability.routes import _loaded_header

    out: list[dict[str, Any]] = []
    _model, header, _error = await _loaded_header()
    if header is not None:
        out.append(compat.gguf_target(header.metadata, header.path))
    if worker.state() == "ready":
        reply = await asyncio.to_thread(worker.request, "status", {}, 30)
        loaded = (
            (reply.get("result") or {}).get("loaded")
            if reply.get("state") == "ran"
            else None
        )
        if loaded:
            out.append(
                {
                    "source": "worker",
                    "label": loaded["repo"],
                    "repo": loaded["repo"],
                    "dModel": loaded.get("dModel"),
                    "layers": loaded.get("layers"),
                }
            )
    return out


@router.get("/library/artifacts")
async def get_artifacts() -> dict[str, Any]:
    targets = await _targets()
    items = []
    for a in artifacts.list_artifacts():
        items.append(
            {
                **a,
                "compat": [
                    {**compat.check(a, t), "target": t["label"], "source": t["source"]}
                    for t in targets
                ],
            }
        )
    return {
        "artifacts": items,
        "targets": targets,
        "usedBytes": artifacts.used_bytes(),
        "budgetBytes": artifacts.budget_bytes(),
    }


@router.delete("/library/artifacts/{artifact_id:path}")
def delete_artifact(artifact_id: str) -> dict[str, Any]:
    if not artifacts.delete_artifact(artifact_id):
        raise HTTPException(status_code=404, detail=f"no artifact {artifact_id}")
    return {"ok": True}


# ── features (SAEs on traces) ────────────────────────────────────────────────


def _trace_or_404(trace_id: str) -> Any:
    from backend.modules.llamacpp import traces

    trace = traces.load(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"no trace {trace_id}")
    return trace


def _trace_target(trace: Any) -> dict[str, Any] | None:
    """The model a trace was recorded on, as a compatibility target."""
    from backend.modules.interpretability.gguf import read_header

    path = str(trace.manifest.get("modelPath") or "")
    try:
        header = read_header(path)
    except Exception:  # noqa: BLE001 — a moved or unreadable file is "unknown"
        return None
    return compat.gguf_target(header.metadata, path)


def _sae_rows(target: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        {
            **a,
            "compat": compat.check(a, target),
            "neuronpedia": catalog.neuronpedia_source(a["entryId"], a["choice"]),
        }
        for a in artifacts.list_artifacts()
        if a["kind"] == "sae"
    ]


def _artifact_or_404(artifact_id: str) -> dict[str, Any]:
    found = artifacts.get_artifact(artifact_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no artifact {artifact_id}")
    return found


@router.get("/features/traces")
def get_feature_traces() -> dict[str, Any]:
    """Traces newest first, each with the installed SAEs that may apply to it
    (compatible or unknown — never incompatible) and whether it recorded their layer."""
    from backend.modules.llamacpp import traces

    by_path: dict[str, dict[str, Any] | None] = {}
    rows = artifacts.list_artifacts()
    out = []
    for trace in traces.list_traces():
        path = str(trace.manifest.get("modelPath") or "")
        if path not in by_path:
            by_path[path] = _trace_target(trace)
        target = by_path[path]
        recorded = trace.manifest.get("layers") or []
        captures_l_out = any(
            str(c).startswith("l_out")
            for c in trace.manifest.get("capture") or ["l_out"]
        )
        fits = []
        for a in rows:
            if a["kind"] != "sae":
                continue
            state = compat.check(a, target)["state"]
            layer = (a.get("facts") or {}).get("layer")
            has_layer = captures_l_out and (not recorded or layer in recorded)
            if state != "incompatible" and has_layer:
                fits.append({"artifactId": a["id"], "state": state})
        out.append(
            {
                "traceId": trace.trace_id,
                "modelName": trace.manifest.get("modelName"),
                "prompt": str(trace.manifest.get("prompt") or "")[:120],
                "createdAt": trace.manifest.get("createdAt"),
                "fits": fits,
            }
        )
    out.sort(key=lambda t: t["createdAt"] or 0, reverse=True)
    return {"traces": out}


@router.get("/features/saes")
def get_saes(traceId: str = "") -> dict[str, Any]:
    """Installed SAEs, each with whether it fits the trace's model."""
    target = _trace_target(_trace_or_404(traceId)) if traceId else None
    return {"target": target, "saes": _sae_rows(target)}


@router.get("/features/{trace_id}")
async def get_features(trace_id: str, artifactId: str) -> dict[str, Any]:
    trace = _trace_or_404(trace_id)
    art = _artifact_or_404(artifactId)
    verdict = compat.check(art, _trace_target(trace))
    if verdict["state"] == "incompatible":
        # Encoding would return numbers; every one of them would be wrong.
        return {"error": "; ".join(verdict["reasons"]), "compat": verdict}
    try:
        model = await asyncio.to_thread(sae.load, art)
        out = await asyncio.to_thread(sae.encode_trace, trace, model)
    except sae.SaeError as exc:
        return {"error": str(exc), "compat": verdict}
    return {
        **out,
        "traceId": trace.trace_id,
        "model": trace.manifest.get("modelName"),
        "compat": verdict,
        "neuronpedia": catalog.neuronpedia_source(art["entryId"], art["choice"]),
    }


@router.get("/features/{trace_id}/feature/{index}")
async def get_feature_values(
    trace_id: str, index: int, artifactId: str
) -> dict[str, Any]:
    trace = _trace_or_404(trace_id)
    art = _artifact_or_404(artifactId)
    try:
        model = await asyncio.to_thread(sae.load, art)
        return await asyncio.to_thread(sae.feature_activations, trace, model, index)
    except sae.SaeError as exc:
        return {"error": str(exc)}


@router.post("/features/label-check")
async def post_label_check(req: LabelCheckRequest) -> dict[str, Any]:
    """Trace Neuronpedia's top example for a feature through the trace's model and
    compare activations. Sends nothing of the user's."""
    trace = _trace_or_404(req.traceId)
    art = _artifact_or_404(req.artifactId)
    np_source = catalog.neuronpedia_source(art["entryId"], art["choice"])
    if np_source is None:
        return {
            "state": "not_checked",
            "reason": "Neuronpedia hosts no labels for this dictionary",
        }
    try:
        return await sae.label_check(
            art,
            req.index,
            np_source["model"],
            np_source["source"],
            str(trace.manifest.get("modelPath") or ""),
        )
    except sae.SaeError as exc:
        return {"state": "not_checked", "reason": str(exc)}
    except httpx.HTTPError as exc:
        return {"state": "not_checked", "reason": f"could not reach Neuronpedia: {exc}"}


# ── attribution graphs ───────────────────────────────────────────────────────


def _graph_or_404(graph_id: str) -> dict[str, Any]:
    art = _artifact_or_404(graph_id)
    if art["kind"] != "graph":
        raise HTTPException(status_code=422, detail=f"{graph_id} is not a graph")
    return art


@router.get("/graphs")
def get_graphs() -> dict[str, Any]:
    return {"graphs": [a for a in artifacts.list_artifacts() if a["kind"] == "graph"]}


@router.post("/graphs/import")
async def post_graph_import(req: GraphImportRequest) -> dict[str, Any]:
    try:
        if req.json_text:
            art = await asyncio.to_thread(graphs.import_json, req.json_text, req.name)
        elif req.ref:
            art = await graphs.import_reference(req.ref)
        else:
            raise HTTPException(status_code=422, detail="give a ref or the graph JSON")
    except graphs.GraphError as exc:
        return {"error": str(exc)}
    except httpx.HTTPError as exc:
        return {"error": f"could not fetch the graph: {exc}"}
    return {"graph": art}


@router.get("/graphs/{graph_id:path}/summary")
async def get_graph_summary(graph_id: str, pinnedOnly: bool = False) -> dict[str, Any]:
    """The strongest paths into the target, with Neuronpedia's labels for the
    features on them when the graph came from there."""
    art = _graph_or_404(graph_id)
    try:
        graph = await asyncio.to_thread(graphs.load, art)
    except graphs.GraphError as exc:
        return {"error": str(exc)}
    first = graphs.summarize(graph, pinned_only=pinnedOnly)
    labels = await graphs.neuronpedia_labels(
        graph, first.get("pathNodes") or [], (art.get("facts") or {}).get("sourceSet")
    )
    return (
        graphs.summarize(graph, pinned_only=pinnedOnly, labels=labels)
        if labels
        else first
    )


@router.put("/graphs/{graph_id:path}/qparams")
def put_graph_qparams(graph_id: str, req: GraphQParams) -> dict[str, Any]:
    art = _graph_or_404(graph_id)
    return {"qParams": graphs.save_qparams(art, req.model_dump())}


@router.get("/graphs/{graph_id:path}")
def get_graph(graph_id: str) -> dict[str, Any]:
    art = _graph_or_404(graph_id)
    try:
        return {"artifact": art, "graph": graphs.load(art)}
    except graphs.GraphError as exc:
        return {"error": str(exc)}


# ── fitted lenses ────────────────────────────────────────────────────────────


@router.get("/lens/targets")
def get_lens_targets(artifactId: str) -> dict[str, Any]:
    """Local GGUFs a downloaded lens matches, and how many others were left out."""
    art = _artifact_or_404(artifactId)
    if art["kind"] != "jlens":
        raise HTTPException(status_code=422, detail=f"{artifactId} is not a lens")
    return lenses.targets(art)


@router.post("/lens/install")
async def post_lens_install(req: LensInstallRequest) -> dict[str, Any]:
    art = _artifact_or_404(req.artifactId)
    if art["kind"] != "jlens":
        raise HTTPException(status_code=422, detail=f"{req.artifactId} is not a lens")
    try:
        return {"installed": await lenses.install(art, req.modelPath)}
    except lenses.LensInstallError as exc:
        return {"error": str(exc)}


# ── steering ─────────────────────────────────────────────────────────────────


@router.post("/steer")
async def post_steer(req: SteerRequest) -> StreamingResponse:
    """Default and steered generations of one prompt, streamed as ndjson, ending
    with `{done, result}`: the two texts, whether the feature arrived, and how the
    first prediction moved."""
    art = _artifact_or_404(req.artifactId)
    base = _trace_or_404(req.traceId) if req.traceId else None
    model_path = req.modelPath or (str(base.manifest.get("modelPath")) if base else "")
    if not model_path:
        raise HTTPException(
            status_code=422, detail="no model: give a traceId or a modelPath"
        )
    prompt = req.prompt or (str(base.manifest.get("prompt") or "") if base else "")
    target = _trace_target(base) if base else None
    verdict = compat.check(art, target)
    if verdict["state"] == "incompatible":
        raise HTTPException(status_code=409, detail="; ".join(verdict["reasons"]))

    # The reference scale when the feature does not fire on this prompt at all:
    # Neuronpedia's maximum, a public GET by id (cached).
    reference: float | None = None
    source = catalog.neuronpedia_source(art["entryId"], art["choice"])
    if source is not None:
        try:
            card = await neuronpedia.feature(
                source["model"], source["source"], req.feature
            )
            reference = card.get("maxActivation")
        except httpx.HTTPError:
            reference = None

    async def gen() -> AsyncIterator[str]:
        try:
            async for event in steer.run(
                art,
                req.feature,
                req.strength,
                model_path=model_path,
                prompt=prompt,
                # Reused as the default only when it is the same experiment
                # (`steer.run` checks prompt, model, length, layer).
                baseline_trace_id=req.traceId,
                max_tokens=req.maxTokens,
                fallback_peak=reference,
            ):
                yield json.dumps(event) + "\n"
        except (steer.SteerError, sae.SaeError) as exc:
            yield json.dumps({"error": str(exc)}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ── neuronpedia ──────────────────────────────────────────────────────────────


@router.get("/neuronpedia/feature")
async def get_feature(model: str, source: str, index: str) -> dict[str, Any]:
    try:
        return await neuronpedia.feature(model, source, index)
    except httpx.HTTPStatusError as exc:
        return {"error": f"Neuronpedia answered {exc.response.status_code}"}
    except httpx.HTTPError as exc:
        return {"error": f"could not reach Neuronpedia: {exc}"}


# ── worker ───────────────────────────────────────────────────────────────────


@router.get("/worker")
async def get_worker() -> dict[str, Any]:
    out: dict[str, Any] = {"env": env.status(), **worker.describe()}
    if worker.state() == "ready":
        ping = await asyncio.to_thread(worker.request, "ping", {}, 30)
        status = await asyncio.to_thread(worker.request, "status", {}, 30)
        out["ping"] = ping.get("result") if ping.get("state") == "ran" else None
        out["status"] = status.get("result") if status.get("state") == "ran" else None
    return out


@router.post("/worker/env/install")
def post_install(req: InstallRequest) -> dict[str, Any]:
    try:
        started = env.start_install(req.group)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"started": started, "env": env.status()}


@router.post("/worker/start")
async def post_start() -> dict[str, Any]:
    token = await artifacts._token()
    try:
        await asyncio.to_thread(worker.start, token)
    except WorkerUnavailable as exc:
        return {"ok": False, "error": str(exc), **worker.describe()}
    return {"ok": True, **worker.describe()}


@router.post("/worker/stop")
async def post_stop() -> dict[str, Any]:
    await asyncio.to_thread(worker.stop)
    return worker.describe()


@router.post("/worker/op")
async def post_op(req: WorkerOpRequest) -> dict[str, Any]:
    if req.op not in WORKER_OPS:
        raise HTTPException(
            status_code=422, detail=f"{req.op} is not callable from here"
        )
    return await asyncio.to_thread(worker.request, req.op, req.args, 900)
