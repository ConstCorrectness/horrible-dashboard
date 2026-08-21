"""HTTP surface for trajectories.

One ingest path (`POST /ingest`) is shared by the Python SDK, every importer and
each internal adapter, so there is exactly one place where a run becomes a row and
exactly one place to fix when the normalisation is wrong.
"""

from __future__ import annotations

import logging

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.modules.trajectories import analyze, export, search, store
from backend.modules.trajectories.adapters import games as games_adapter
from backend.modules.trajectories.adapters import importers
from backend.modules.trajectories.models import (
    CreateDataset,
    Dataset,
    DatasetListResponse,
    Harness,
    HarnessListResponse,
    IngestRequest,
    IngestResponse,
    LabelWrite,
    RunListResponse,
    TrajectoryDetail,
    TrajectoryLabel,
    UpdateDataset,
)

logger = logging.getLogger("trajectories")

router = APIRouter(prefix="/trajectories", tags=["trajectories"])


# --- datasets ---------------------------------------------------------------


@router.get("/datasets", response_model=DatasetListResponse)
async def list_datasets() -> DatasetListResponse:
    return DatasetListResponse(datasets=store.list_datasets())


@router.post("/datasets", response_model=Dataset)
async def create_dataset(body: CreateDataset) -> Dataset:
    if store.get_dataset(body.id) is not None:
        raise HTTPException(status_code=409, detail=f"dataset {body.id} already exists")
    return store.create_dataset(
        body.id,
        body.name,
        description=body.description,
        source_kind=body.source_kind,
        capture=body.capture,
        tags=body.tags,
    )


@router.get("/datasets/{dataset_id}", response_model=Dataset)
async def get_dataset(dataset_id: str) -> Dataset:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


@router.patch("/datasets/{dataset_id}", response_model=Dataset)
async def update_dataset(dataset_id: str, body: UpdateDataset) -> Dataset:
    if store.get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    # At most one dataset captures at a time: two would make "where did this run
    # go" ambiguous, and the recorder picks one anyway.
    if body.capture:
        for other in store.list_datasets():
            if other.id != dataset_id and other.capture:
                store.update_dataset(other.id, capture=False)
    updated = store.update_dataset(
        dataset_id,
        name=body.name,
        description=body.description,
        capture=body.capture,
        tags=body.tags,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return updated


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: str) -> dict[str, bool]:
    return {"ok": store.delete_dataset(dataset_id)}


# --- runs -------------------------------------------------------------------


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    dataset: str | None = None,
    source: str | None = None,
    harness: str | None = None,
    outcome: str | None = None,
    agent: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> RunListResponse:
    runs, total = store.list_runs(
        dataset_id=dataset,
        source=source,
        harness=harness,
        outcome=outcome,
        agent_id=agent,
        status=status,
        q=q,
        limit=limit,
        offset=offset,
    )
    return RunListResponse(runs=runs, total=total)


@router.get("/runs/{run_id}", response_model=TrajectoryDetail)
async def get_run(run_id: str, steps: bool = True) -> TrajectoryDetail:
    run = store.get_run(run_id, with_steps=steps)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str) -> dict[str, bool]:
    return {"ok": store.delete_run(run_id)}


@router.post("/runs/{run_id}/labels", response_model=TrajectoryLabel)
async def add_label(run_id: str, body: LabelWrite) -> TrajectoryLabel:
    if store.get_run(run_id, with_steps=False) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return store.add_label(run_id, body)


# --- ingest -----------------------------------------------------------------


def _ingest_all(writes: list[Any]) -> IngestResponse:
    """Write a batch and report it. Shared by `/ingest`, `/import` and the replay
    importer so there is one place a run becomes a row."""
    run_ids: list[str] = []
    created = 0
    for write in writes:
        run_id, is_new = store.ingest_run(write)
        run_ids.append(run_id)
        created += 1 if is_new else 0
    return IngestResponse(
        run_ids=run_ids, created=created, merged=len(run_ids) - created
    )


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest) -> IngestResponse:
    result = _ingest_all(body.runs)
    # Indexed here, awaited, because an ingest is already a bulk operation: one
    # whole-table merge for the whole batch. Failure is logged and dropped — a
    # missing index entry is a `POST /reindex` away, a rejected ingest is data
    # the caller may not still have.
    try:
        await search.index_runs(result.run_ids)
    except Exception:
        logger.debug("trajectories: post-ingest indexing failed", exc_info=True)
    return result


# --- harnesses --------------------------------------------------------------


@router.get("/harnesses", response_model=HarnessListResponse)
async def list_harnesses(limit: int = Query(100, ge=1, le=500)) -> HarnessListResponse:
    return HarnessListResponse(harnesses=store.list_harnesses(limit))


@router.get("/harnesses/{fingerprint}", response_model=Harness)
async def get_harness(fingerprint: str) -> Harness:
    harness = store.get_harness(fingerprint)
    if harness is None:
        raise HTTPException(status_code=404, detail="harness not found")
    return harness


# --- aggregates -------------------------------------------------------------


@router.get("/stats")
async def stats(dataset: str | None = None) -> dict[str, Any]:
    """Headline counts plus the top tools. Deliberately not `response_model`-typed:
    the shape is a dashboard payload, and pinning it here would mean editing two
    files every time a tile is added."""
    return analyze.dataset_stats(dataset)


@router.get("/tools")
async def tools(
    dataset: str | None = None,
    harness: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    return {
        "tools": analyze.tool_stats(dataset_id=dataset, harness=harness, limit=limit)
    }


@router.get("/compare")
async def compare(a: str, b: str) -> dict[str, Any]:
    """Compare two harnesses. Reports `comparable: false` when the two did not run
    on enough of the same goals for the difference to mean anything."""
    for fingerprint in (a, b):
        if store.get_harness(fingerprint) is None:
            raise HTTPException(status_code=404, detail=f"no harness {fingerprint}")
    return analyze.compare(a, b)


# --- search -----------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = ""
    dataset: str | None = None
    #: Successes only unless asked otherwise — retrieving failures as worked
    #: examples teaches the failure. Pass null to search everything.
    outcome: str | None = "success"
    harness: str | None = None
    limit: int = 5


@router.post("/search")
async def search_runs(body: SearchRequest) -> dict[str, Any]:
    """Semantic search over runs.

    `method` in the response says whether you got `semantic`, `substring` (no
    embedder answered) or `recent` (empty query) — a caller is told which it got
    rather than handed silently worse results.
    """
    runs, method = await search.search_runs(
        body.query,
        limit=max(1, min(body.limit, 50)),
        dataset_id=body.dataset,
        outcome=body.outcome,
        harness=body.harness,
    )
    return {"runs": [r.model_dump() for r in runs], "method": method}


@router.post("/reindex")
async def reindex(dataset: str | None = None, full: bool = False) -> dict[str, int]:
    """Build the vector index. `full` drops the collection and rebuilds."""
    return await search.reindex(dataset, full=full)


# --- export -----------------------------------------------------------------


class ExportRequest(BaseModel):
    name: str = "trajectories"
    dataset: str | None = None
    harness: str | None = None
    #: Require the outcome label to come from this source. `human` is the one to
    #: use when the training run matters — an `agent-critic` label is a model
    #: grading a model.
    label_source: str | None = None
    min_score: float | None = None
    limit: int = 1000


@router.post("/export")
async def export_sft(body: ExportRequest) -> dict[str, Any]:
    """Write an SFT JSONL file of graded successes. Reports what it skipped."""
    return export.export_dataset(
        name=body.name,
        dataset_id=body.dataset,
        harness=body.harness,
        label_source=body.label_source,
        min_score=body.min_score,
        limit=max(1, min(body.limit, 10000)),
    )


# --- import -----------------------------------------------------------------


class ImportRequest(BaseModel):
    dataset_id: str
    #: One of `importers.FORMATS`.
    format: str
    content: str


@router.post("/import", response_model=IngestResponse)
async def import_file(body: ImportRequest) -> IngestResponse:
    try:
        writes = importers.import_any(body.format, body.content, body.dataset_id)
    except importers.ImportFormatError as exc:
        # 400, not 500: the payload is wrong, not the server.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ingest_all(writes)


@router.post("/import/replay/{replay_id}", response_model=IngestResponse)
async def import_replay(replay_id: str, dataset_id: str = "games") -> IngestResponse:
    """Import a games replay — one run per seat."""
    from backend.modules.games import server_auth

    replay = await server_auth.replay_get(replay_id)
    if not replay or replay.get("error"):
        raise HTTPException(
            status_code=404, detail=str(replay.get("error") if replay else "no replay")
        )
    return _ingest_all(games_adapter.replay_to_writes(replay, dataset_id))
