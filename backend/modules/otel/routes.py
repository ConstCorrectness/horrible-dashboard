"""OTLP/HTTP receiver + read API for stored spans.

The receiver lives at `/api/otel/v1/traces` so that an unmodified exporter pointed
at `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:<port>/api/otel` finds it — the
SDKs append `/v1/traces` to the base endpoint themselves. (Setting
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` instead is taken verbatim, so it must be the
full `/api/otel/v1/traces`.) HTTP only; gRPC on :4317 is out of scope.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend import server_port
from backend.modules.otel import auth, decode, export, ids, materialize, store
from backend.modules.otel.models import IngestInfo, Span, TraceSummary

router = APIRouter(prefix="/otel", tags=["otel"])

#: Raw (possibly gzipped) body ceiling. The inflated ceiling is in `decode`.
MAX_BODY_BYTES = 32 * 1024 * 1024

_background: set[asyncio.Task[None]] = set()


def local_endpoint() -> str:
    return f"http://127.0.0.1:{server_port.port()}/api/otel"


def _is_json(content_type: str | None) -> bool:
    return (content_type or "").split(";", 1)[0].strip().lower() in (
        "application/json",
        "text/json",
    )


@router.post("/v1/traces", dependencies=[Depends(auth.require_ingest)])
async def export_traces(request: Request) -> Response:
    declared = int(request.headers.get("content-length") or 0)
    if declared > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="body too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="body too large")
    content_type = request.headers.get("content-type")
    try:
        raw = decode.inflate(body, request.headers.get("content-encoding"))
        spans = decode.decode(raw, content_type)
    except decode.DecodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    touched = await asyncio.to_thread(store.insert_spans, spans, origin="received")
    await asyncio.to_thread(materialize.touch, touched)
    if spans:
        # Detached: a slow external collector must not hold up the sender's export.
        task = asyncio.create_task(
            export.forward_received(
                body, content_type, request.headers.get("content-encoding")
            )
        )
        # The loop holds tasks weakly; an unreferenced one can vanish mid-send.
        _background.add(task)
        task.add_done_callback(_background.discard)

    # An empty ExportTraceServiceResponse means "all accepted" in both encodings.
    if _is_json(content_type):
        return Response(content=b"{}", media_type="application/json")
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceResponse,
    )

    return Response(
        content=ExportTraceServiceResponse().SerializeToString(),
        media_type="application/x-protobuf",
    )


@router.get("/ingest", response_model=IngestInfo)
def ingest_info(request: Request) -> IngestInfo:
    last, count = store.received_stats()
    loopback = auth.client_is_loopback(request)
    return IngestInfo(
        endpoint=local_endpoint(),
        token=auth.get_token() if loopback else None,
        last_received_at=last,
        received_traces=count,
    )


@router.post(
    "/ingest/rotate",
    response_model=IngestInfo,
    dependencies=[Depends(auth.require_loopback)],
)
def rotate_ingest_token() -> IngestInfo:
    last, count = store.received_stats()
    return IngestInfo(
        endpoint=local_endpoint(),
        token=auth.rotate_token(),
        last_received_at=last,
        received_traces=count,
    )


@router.get("/traces", response_model=list[TraceSummary])
def list_traces(origin: str | None = None, limit: int = 50) -> list[TraceSummary]:
    if origin not in (None, "received", "local"):
        raise HTTPException(status_code=400, detail="origin must be received|local")
    return store.list_traces(origin=origin, limit=max(1, min(limit, 500)))  # type: ignore[arg-type]


@router.get("/traces/{trace_id}", response_model=list[Span])
def get_trace(trace_id: str) -> list[Span]:
    return store.get_trace(trace_id.lower())


@router.delete("/traces/{trace_id}")
def delete_trace(trace_id: str) -> dict[str, int]:
    return {"deleted": store.delete_trace(trace_id.lower())}


@router.get("/turn/{turn_id}", response_model=list[Span])
def get_turn_trace(turn_id: str) -> list[Span]:
    """Spans of a built-in turn — including any a user's MCP server sent back
    under the propagated trace id."""
    return store.get_trace(ids.trace_id_for_turn(turn_id))
