"""FastAPI routes for the LocalTrack experiment tracking module."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from backend.modules.localtrack import store
from backend.modules.localtrack.models import (
    ArtifactListResponse,
    BatchIngestRequest,
    BatchIngestResponse,
    CreateProjectRequest,
    CreateRunRequest,
    MetricQueryRequest,
    MetricQueryResponse,
    ProjectListResponse,
    ProjectModel,
    RunArtifactModel,
    RunListResponse,
    RunModel,
    UpdateRunRequest,
)

router = APIRouter(tags=["localtrack"])


# --- Panel layout ---


@router.get("/api/localtrack/projects/{project_id}/layout")
async def get_layout(project_id: str) -> dict[str, Any]:
    """The saved panel arrangement for a project.

    `panels: null` means "never saved one" and the pane should use its defaults;
    `panels: []` means the user removed every panel. Two different answers — see
    `store.get_layout`.
    """
    return {"panels": store.get_layout(project_id)}


@router.put("/api/localtrack/projects/{project_id}/layout")
async def put_layout(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    panels = body.get("panels")
    if not isinstance(panels, list):
        raise HTTPException(status_code=400, detail="panels must be a list")
    store.save_layout(project_id, panels)
    return {"ok": True}


# --- Projects Endpoints ---


@router.get("/api/localtrack/projects", response_model=ProjectListResponse)
async def list_projects() -> ProjectListResponse:
    """List all projects with run counts and activity timestamps."""
    return ProjectListResponse(projects=store.list_projects())


@router.post("/api/localtrack/projects", response_model=ProjectModel)
async def create_project(req: CreateProjectRequest) -> ProjectModel:
    """Create or update a project."""
    return store.create_project(
        project_id=req.id,
        name=req.name,
        description=req.description,
    )


@router.get("/api/localtrack/projects/{project_id}", response_model=ProjectModel)
async def get_project(project_id: str) -> ProjectModel:
    """Get project details."""
    proj = store.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return proj


@router.delete("/api/localtrack/projects/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    """Delete a project and its associated runs/metrics."""
    if project_id == "default":
        raise HTTPException(status_code=400, detail="Cannot delete default project")
    ok = store.delete_project(project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "ok", "deleted": project_id}


# --- Runs Endpoints ---


@router.get("/api/localtrack/runs", response_model=RunListResponse)
async def list_runs(
    project_id: str | None = Query(None, description="Filter by project ID"),
) -> RunListResponse:
    """List experiment runs."""
    return RunListResponse(runs=store.list_runs(project_id=project_id))


@router.post("/api/localtrack/runs", response_model=RunModel)
async def create_run(req: CreateRunRequest) -> RunModel:
    """Create a new experiment run."""
    return store.create_run(
        run_id=req.id,
        project_id=req.project_id or "default",
        name=req.name,
        config=req.config,
        system_info=req.system_info,
        tags=req.tags,
    )


@router.get("/api/localtrack/runs/{run_id}", response_model=RunModel)
async def get_run(run_id: str) -> RunModel:
    """Get a single run by ID."""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.patch("/api/localtrack/runs/{run_id}", response_model=RunModel)
async def update_run(run_id: str, req: UpdateRunRequest) -> RunModel:
    """Update run status, config, summary, or metadata."""
    run = store.update_run(
        run_id=run_id,
        name=req.name,
        status=req.status,
        config=req.config,
        summary=req.summary,
        tags=req.tags,
        end_time=req.end_time,
        duration_seconds=req.duration_seconds,
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.delete("/api/localtrack/runs/{run_id}")
async def delete_run(run_id: str) -> dict[str, Any]:
    """Delete a run and all its metrics/artifacts."""
    ok = store.delete_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "ok", "deleted": run_id}


# --- Metrics Ingestion & Query Endpoints ---


@router.post("/api/localtrack/metrics/ingest", response_model=BatchIngestResponse)
async def batch_ingest_metrics(req: BatchIngestRequest) -> BatchIngestResponse:
    """High-throughput batch ingestion of metric logs."""
    count = store.ingest_metrics(req.logs)
    return BatchIngestResponse(ingested_count=count, status="ok")


@router.get("/api/localtrack/metrics/keys")
async def get_metric_keys(
    project_id: str | None = Query(None),
    run_ids: str | None = Query(None, description="Comma-separated list of run IDs"),
) -> dict[str, list[str]]:
    """Discover distinct metric keys across selected runs or project."""
    r_list = [r.strip() for r in run_ids.split(",") if r.strip()] if run_ids else None
    keys = store.get_metric_keys(run_ids=r_list, project_id=project_id)
    return {"keys": keys}


@router.post("/api/localtrack/metrics/query", response_model=MetricQueryResponse)
async def query_metrics(req: MetricQueryRequest) -> MetricQueryResponse:
    """Fetch time-series metric series data with LTTB downsampling and EMA smoothing."""
    series = store.query_metrics(
        run_ids=req.run_ids,
        keys=req.keys,
        max_points=req.max_points,
        smoothing=req.smoothing,
        min_step=req.min_step,
        max_step=req.max_step,
    )
    return MetricQueryResponse(series=series)


# --- Run Artifacts Endpoints ---


@router.post("/api/localtrack/runs/{run_id}/artifacts", response_model=RunArtifactModel)
async def upload_artifact(
    run_id: str,
    file: UploadFile = File(...),
) -> RunArtifactModel:
    """Upload an artifact file (e.g. config.json, trainer_state.json) for a run."""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    content = await file.read()
    filename = file.filename or "artifact.bin"
    mime, _ = mimetypes.guess_type(filename)

    return store.save_artifact(
        run_id=run_id,
        filename=filename,
        content=content,
        content_type=mime or "application/octet-stream",
    )


@router.get("/api/localtrack/runs/{run_id}/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(run_id: str) -> ArtifactListResponse:
    """List all artifacts for a run."""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return ArtifactListResponse(artifacts=store.list_artifacts(run_id))


@router.get("/api/localtrack/runs/{run_id}/artifacts/{artifact_id}/download")
async def download_artifact(run_id: str, artifact_id: str) -> FileResponse:
    """Download a run artifact."""
    art = store.get_artifact(artifact_id)
    if not art or art.run_id != run_id:
        raise HTTPException(status_code=404, detail="Artifact not found")

    p = Path(art.file_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Artifact file missing from disk")

    return FileResponse(
        path=str(p),
        filename=art.filename,
        media_type=art.content_type,
    )
