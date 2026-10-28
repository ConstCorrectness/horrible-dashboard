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

from backend.modules.interp import artifacts, catalog, compat, env, neuronpedia
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
