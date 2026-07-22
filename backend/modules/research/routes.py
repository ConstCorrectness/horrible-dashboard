"""HTTP surface for the research module (`/api/research/*`).

Capture and PDF-fetch are synchronous from the caller's point of view (the blob
is stored before the response returns); the library *ingestion* of the stored
artifact then proceeds on the task queue with status on the `library` channel,
same as every other source.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Response

from backend.modules.artifacts import store as artifacts_store
from backend.modules.artifacts.models import ArtifactModel
from backend.modules.artifacts.store import artifact_path
from backend.modules.browser.fetch import UnsafeUrlError
from backend.modules.library import store as library_store
from backend.modules.library.models import SourceModel
from backend.modules.research.broadcast import publish_run
from backend.modules.research.models import (
    CaptureRequest,
    CaptureResponse,
    ExportRequest,
    ExportResponse,
    RunModel,
    RunsListResponse,
    SavePdfRequest,
    StartRunRequest,
    StepModel,
    StepsListResponse,
)
from backend.modules.research import obsidian, runstore, service
from backend.modules.research.runner import research_runner
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research"])


@router.post("/capture", response_model=CaptureResponse)
async def capture(req: CaptureRequest) -> CaptureResponse:
    try:
        result = await service.capture_url(
            req.url, library=req.library, title=req.title, tags=req.tags
        )
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CaptureResponse(
        artifact=ArtifactModel(**result["artifact"]),
        source=SourceModel(**result["source"]),
    )


@router.post("/export", response_model=ExportResponse)
def export(req: ExportRequest) -> ExportResponse:
    """Export a stored source/artifact into the configured Obsidian vault."""
    source = None
    artifact_id = req.artifact_id
    if req.source_id:
        source = library_store.get_source(req.source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        artifact_id = source.get("artifact_id") or artifact_id
    if not artifact_id:
        raise HTTPException(
            status_code=400, detail="source_id or artifact_id is required"
        )
    artifact = artifacts_store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    try:
        result = obsidian.export_source(source, artifact)
    except obsidian.ObsidianNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ExportResponse(
        note_path=result["note_path"], attachment_path=result["attachment_path"]
    )


# --- deep-research runs -----------------------------------------------------


@router.post("/runs", response_model=RunModel)
def start_run(req: StartRunRequest) -> RunModel:
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    if req.effort not in ("auto", "quick", "standard", "deep"):
        raise HTTPException(status_code=400, detail=f"unknown effort: {req.effort}")
    budget = int(get_value("research.tokenBudget", 200_000) or 200_000)
    run = runstore.create_run(
        query=query,
        effort=req.effort,
        library=req.library or "default",
        provider=req.provider,
        model=req.model,
        token_budget=budget,
    )
    research_runner.enqueue(run["id"])
    publish_run(run)
    return RunModel(**run)


@router.get("/runs", response_model=RunsListResponse)
def list_runs() -> RunsListResponse:
    return RunsListResponse(runs=[RunModel(**r) for r in runstore.list_runs()])


def _run_or_404(run_id: str) -> dict:
    run = runstore.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("/runs/{run_id}", response_model=RunModel)
def get_run(run_id: str) -> RunModel:
    return RunModel(**_run_or_404(run_id))


@router.get("/runs/{run_id}/steps", response_model=StepsListResponse)
def get_steps(run_id: str, transcript: bool = False) -> StepsListResponse:
    _run_or_404(run_id)
    steps = runstore.list_steps(run_id, include_transcript=transcript)
    return StepsListResponse(steps=[StepModel(**s) for s in steps])


@router.post("/runs/{run_id}/cancel", response_model=RunModel)
def cancel_run(run_id: str) -> RunModel:
    run = _run_or_404(run_id)
    if run["status"] in ("done", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"run is already {run['status']}")
    runstore.request_cancel(run_id)
    run = _run_or_404(run_id)
    publish_run(run)
    return RunModel(**run)


@router.post("/runs/{run_id}/retry", response_model=RunModel)
def retry_run(run_id: str) -> RunModel:
    """Re-enqueue a failed/cancelled run: failed steps get a fresh attempt
    budget; completed steps keep their checkpoints."""
    run = _run_or_404(run_id)
    if run["status"] not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"only failed/cancelled runs retry, not {run['status']}",
        )
    runstore.reset_failed_steps(run_id)
    runstore.reset_running_steps(run_id)
    with runstore.get_db_conn() as conn:
        conn.execute(
            "UPDATE research_runs SET status = 'pending', error = NULL, "
            "cancel_requested = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (run_id,),
        )
    run = _run_or_404(run_id)
    research_runner.enqueue(run_id)
    publish_run(run)
    return RunModel(**run)


@router.get("/runs/{run_id}/report")
def get_report(run_id: str) -> Response:
    run = _run_or_404(run_id)
    if not run["report_artifact_id"]:
        raise HTTPException(status_code=409, detail="run has no report yet")
    path = artifact_path(run["report_artifact_id"])
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="report artifact missing")
    return Response(
        content=path.read_text(encoding="utf-8"), media_type="text/markdown"
    )


@router.post("/pdf", response_model=CaptureResponse)
async def save_pdf(req: SavePdfRequest) -> CaptureResponse:
    try:
        result = await service.save_pdf_url(
            req.url, library=req.library, title=req.title, tags=req.tags
        )
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
    return CaptureResponse(
        artifact=ArtifactModel(**result["artifact"]),
        source=SourceModel(**result["source"]),
    )
