"""The span shape every path normalizes to — OTLP protobuf, OTLP JSON, our tracer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

SpanOrigin = Literal["received", "local"]

#: OTLP `Status.StatusCode`.
STATUS_UNSET = 0
STATUS_OK = 1
STATUS_ERROR = 2


class SpanEvent(BaseModel):
    name: str
    time_ns: int = 0
    attrs: dict[str, Any] = {}


class Span(BaseModel):
    """One span, ids as lowercase hex. `attrs`/`resource` are flat OTel attribute
    maps (OpenInference's `llm.input_messages.0.message.role` stays a flat key —
    un-flattening is the mapper's business, not storage's)."""

    trace_id: str
    span_id: str
    parent_span_id: str = ""
    name: str = ""
    #: OTLP `SpanKind` (0 unspecified, 1 internal, 2 server, 3 client, …).
    kind: int = 0
    start_ns: int = 0
    end_ns: int = 0
    status_code: int = STATUS_UNSET
    status_message: str = ""
    attrs: dict[str, Any] = {}
    events: list[SpanEvent] = []
    resource: dict[str, Any] = {}
    scope: str = ""
    origin: SpanOrigin = "received"
    received_at: float = 0.0


class TraceSummary(BaseModel):
    trace_id: str
    spans: int
    service: str = ""
    root_name: str = ""
    origin: SpanOrigin = "received"
    start_ns: int = 0
    end_ns: int = 0
    errors: int = 0
    received_at: float = 0.0


class IngestInfo(BaseModel):
    """What the "Connect an agent" panel renders. `token` is only ever filled for
    a loopback caller — see `auth.py`."""

    endpoint: str
    protocol: str = "http/protobuf"
    token: str | None = None
    token_required_remote: bool = True
    last_received_at: float | None = None
    received_traces: int = 0
