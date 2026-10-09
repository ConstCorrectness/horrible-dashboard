"""The span shape every path normalizes to — OTLP protobuf, OTLP JSON, our tracer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

#: Where a stored span came from: an OTLP sender (`received`), this node's own
#: tracer (`local`), or a friend's node, returned with the answer to a request
#: this node sent it (`peer`, see `peer_spans.py`).
SpanOrigin = Literal["received", "local", "peer"]

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


class ExportDestination(BaseModel):
    """One place the node's spans are exported to, and how the last export went.
    Never carries a credential — see `destinations.py`."""

    name: str
    label: str = ""
    host: str = ""
    include_content: bool = False
    detail: dict[str, str] = {}
    last_ok_at: float | None = None
    last_error: str = ""
    last_error_at: float | None = None
    spans_ok: int = 0
    spans_failed: int = 0
    #: The most recent outcome was a failure.
    failing: bool = False


class IngestInfo(BaseModel):
    """What the "Connect an agent" panel renders. `token` is only ever filled for
    a loopback caller — see `auth.py`."""

    endpoint: str
    protocol: str = "http/protobuf"
    token: str | None = None
    token_required_remote: bool = True
    last_received_at: float | None = None
    received_traces: int = 0
    #: `host:port` when the opt-in OTLP/gRPC receiver is actually listening. None
    #: means "not serving", which includes "configured but the port was taken".
    grpc_endpoint: str | None = None
