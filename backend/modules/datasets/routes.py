"""HTTP surface for the datasets module.

Every source call is synchronous by contract (they mirror the training providers),
so each goes through `asyncio.to_thread` — which is also what makes the Hub source's
internal `asyncio.run` legal, since the worker thread has no running loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.modules.datasets import builder, formats, registry, sources, tokens
from backend.modules.datasets.models import (
    AdaptRequest,
    BuildRequest,
    DatasetModel,
    DatasetRefModel,
    PeekModel,
    PeekRequest,
    PreviewModel,
    PreviewRequest,
    RegisterRequest,
    SplitModel,
    TokenStatsModel,
    TokenStatsRequest,
    UpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _fail(exc: Exception) -> HTTPException:
    """A source failure is the user's problem to see, not a 500.

    The Hub's own messages name the actual issue ("Dataset is gated", "Config name
    is missing"); replacing them with a stack trace throws away the only useful
    part.
    """
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/sources")
async def list_sources() -> list[dict[str, Any]]:
    return [
        {"id": s.id, "label": s.label, "local": bool(getattr(s, "local", False))}
        for s in sorted(sources.all_sources().values(), key=lambda s: s.label)
    ]


@router.get("/search")
async def search(
    q: str = "", source: str = "hub", limit: int = 20
) -> list[DatasetRefModel]:
    try:
        found = sources.get_source(source)
        return await asyncio.to_thread(found.search, q, max(1, min(limit, 100)))
    except sources.SourceError as exc:
        raise _fail(exc) from exc


@router.get("/splits")
async def splits(ref: str, source: str = "hub") -> list[SplitModel]:
    try:
        found = sources.get_source(source)
        rows = await asyncio.to_thread(found.splits, ref)
    except sources.SourceError as exc:
        raise _fail(exc) from exc
    return [SplitModel(**row) for row in rows]


async def _peek_rows(
    source_id: str, ref: str, config: str, split: str, limit: int
) -> tuple[list[str], list[dict[str, Any]]]:
    found = sources.get_source(source_id)
    return await asyncio.to_thread(found.peek, ref, config, split, limit)


@router.post("/peek")
async def peek(request: PeekRequest) -> PeekModel:
    """Real columns, real rows, and the format verdict they support."""
    try:
        columns, rows = await _peek_rows(
            request.source,
            request.ref,
            request.config,
            request.split,
            max(1, min(request.limit, 50)),
        )
    except sources.SourceError as exc:
        raise _fail(exc) from exc
    return PeekModel(
        source=request.source,
        id=request.ref,
        config=request.config,
        split=request.split,
        columns=columns,
        rows=rows,
        detection=formats.detect(columns, rows).to_dict(),
    )


async def _resolve(
    dataset_id: str, source: str, ref: str, config: str, split: str
) -> tuple[str, str, str, str, dict[str, str], str]:
    """(source, ref, config, split, column_map, format) for a request that may name
    a registered dataset *or* a raw ref. Registered wins — its column map is the
    user's correction and must not be re-guessed."""
    if dataset_id:
        found = registry.get(dataset_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"no dataset {dataset_id!r}")
        return (
            found.source,
            found.ref,
            found.config,
            found.split,
            found.column_map,
            found.format,
        )
    return source, ref, config, split, {}, ""


@router.post("/adapt")
async def adapt(request: AdaptRequest) -> dict[str, Any]:
    """Can this task train on this dataset, and with which columns?"""
    src, ref, config, split, saved_map, saved_fmt = await _resolve(
        request.dataset_id, request.source, request.ref, request.config, request.split
    )
    try:
        columns, rows = await _peek_rows(src, ref, config, split, 5)
    except sources.SourceError as exc:
        raise _fail(exc) from exc
    detection = formats.detect(columns, rows)
    if saved_fmt and saved_fmt != "unknown":
        # A registered dataset carries a verdict a human may have overruled.
        # Re-detecting and ignoring that would quietly undo the correction.
        detection = formats.Detection(
            saved_fmt,
            1.0,
            "as registered (overrides detection)",
            saved_map or detection.columns,
        )
    adaptation = formats.adapt(detection, request.task)
    return {
        "detection": detection.to_dict(),
        "adaptation": adaptation.to_dict(),
        "textField": formats.text_field_for(detection.format, adaptation.columns),
        "formattingSource": (
            formats.formatting_source(detection.format, adaptation.columns)
            if adaptation.needs_formatting
            else ""
        ),
        "columns": columns,
    }


@router.post("/token-stats")
async def token_stats(request: TokenStatsRequest) -> TokenStatsModel:
    src, ref, config, split, saved_map, saved_fmt = await _resolve(
        request.dataset_id, request.source, request.ref, request.config, request.split
    )
    try:
        columns, rows = await _peek_rows(
            src, ref, config, split, max(1, min(request.limit, 500))
        )
    except sources.SourceError as exc:
        raise _fail(exc) from exc
    detection = formats.detect(columns, rows)
    fmt = saved_fmt or detection.format
    return await tokens.token_stats(
        rows,
        fmt=fmt,
        columns=saved_map or detection.columns,
        model=request.model,
        max_length=max(1, request.max_length),
    )


@router.get("")
async def list_datasets(source: str = "") -> list[DatasetModel]:
    return await asyncio.to_thread(registry.list_datasets, source)


@router.post("")
async def register(request: RegisterRequest) -> DatasetModel:
    """Save a dataset definition, detecting its format when none was given."""
    fmt, column_map = request.format, dict(request.column_map)
    rows_count = request.rows
    if not fmt or not column_map:
        try:
            columns, rows = await _peek_rows(
                request.source, request.ref, request.config, request.split, 5
            )
        except sources.SourceError as exc:
            # Registering a dataset we cannot currently reach is legitimate (the
            # Hub may be down, the file may not be fetched yet). It is saved as
            # `unknown` rather than refused, and the pane can detect later.
            logger.info(
                "datasets: registering %s without a peek (%s)", request.ref, exc
            )
            columns, rows = [], []
        detection = formats.detect(columns, rows)
        fmt = fmt or detection.format
        column_map = column_map or detection.columns

    path = ""
    try:
        path = await asyncio.to_thread(
            sources.get_source(request.source).locate, request.ref
        )
    except sources.SourceError:
        path = request.path
    return await asyncio.to_thread(
        registry.register,
        name=request.name,
        source=request.source,
        ref=request.ref,
        config=request.config,
        split=request.split,
        fmt=fmt,
        column_map=column_map,
        rows=rows_count,
        path=path or request.path,
        notes=request.notes,
    )


@router.get("/{dataset_id}")
async def get_dataset(dataset_id: str) -> DatasetModel:
    found = await asyncio.to_thread(registry.get, dataset_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no dataset {dataset_id!r}")
    return found


@router.put("/{dataset_id}")
async def update_dataset(dataset_id: str, request: UpdateRequest) -> DatasetModel:
    updated = await asyncio.to_thread(
        registry.update, dataset_id, **request.model_dump(exclude_none=True)
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"no dataset {dataset_id!r}")
    return updated


@router.delete("/{dataset_id}")
async def delete_dataset(dataset_id: str) -> dict[str, bool]:
    return {"ok": await asyncio.to_thread(registry.delete, dataset_id)}


# --- builder -----------------------------------------------------------------


@router.post("/builder/validate")
async def validate_pipeline(request: PreviewRequest) -> dict[str, Any]:
    """Coherence without touching data — milliseconds, and catches most mistakes."""
    return {"problems": builder.validate(request.pipeline)}


@router.post("/builder/preview")
async def preview_pipeline(request: PreviewRequest) -> PreviewModel:
    return await asyncio.to_thread(
        builder.preview, request.pipeline, max(1, min(request.limit, 100))
    )


@router.post("/builder/build")
async def build_pipeline(request: BuildRequest) -> dict[str, str]:
    try:
        build_id = builder.start_build(request.pipeline, request.name, request.save)
    except builder.BuildError as exc:
        raise _fail(exc) from exc
    return {"buildId": build_id}


@router.get("/builder/status")
async def builder_status(build_id: str = "") -> list[dict[str, Any]]:
    return builder.build_status(build_id)
