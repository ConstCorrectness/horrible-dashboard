"""A peer's half of a trace: returned with its answer, stored and re-exported here.

When this node's agent asks a friend's agent something (`agent.ask_peer`), the
friend's turn already continues our trace — the request carries a W3C
`traceparent`. But its spans stay on the friend's node: our trace view shows an
`execute_tool agent.ask_peer` span with nothing under it, and our Opik project gets
a call to "somewhere" with no inside.

So the callee, when it allows it (`network.returnTraces`, off by default), captures
the spans of that one request and sends them back inside its `agent_result`. The
caller validates them, stores them as `origin="peer"`, and passes them to its own
export destinations — so one trace shows both sides, and the friend needs no
credentials to our Opik.

## What is sent

OTLP protobuf, gzipped, base64 — the format every OTel tool already reads — at most
`MAX_ENCODED` bytes. **Never content**: prompts and tool payloads are stripped
whatever either node's capture settings say, because the friend agreed to share
the shape of the work, not the conversation. `opik.*` attributes are stripped too.

## What is accepted

A returned span is data from another machine. Only spans of **the trace id we
sent**, hanging (transitively) off **the span id we sent**, are kept; a span that
claims one of our own span ids is dropped rather than allowed to overwrite it; and
the resource is stamped with the peer's node id **as authenticated by the fabric**,
never as the payload claims it.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import time
from typing import Any

logger = logging.getLogger("otel")

ENCODING = "otlp-pb-gzip-b64"
#: The base64 payload's ceiling. The dialing side's websocket caps a frame at
#: 1 MiB and the answer rides in the same frame, so this leaves wide room.
MAX_ENCODED = 256 * 1024
#: What a returned payload may inflate to, and how many spans it may hold.
MAX_INFLATED = 4 * 1024 * 1024
MAX_SPANS = 512
#: How far a returned span's clock may sit from ours before it is dropped.
_CLOCK_SLACK_NS = 24 * 3600 * 1_000_000_000


def _setting(key: str, default: bool) -> bool:
    try:
        from backend.modules.settings import get_value

        return bool(get_value(key, default))
    except Exception:  # noqa: BLE001
        return default


def returns_enabled() -> bool:
    """Whether this node sends a requester the spans of its answer."""
    return _setting("network.returnTraces", False)


def _clean_attrs(attrs: Any) -> dict[str, Any]:
    from backend.modules.otel import tracing

    return {
        k: v
        for k, v in (attrs or {}).items()
        if k not in tracing.CONTENT_ATTRS and not k.startswith("opik.")
    }


# --- callee: collect ----------------------------------------------------------------


def _encode(spans: list[Any]) -> str:
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

    raw = encode_spans(spans).SerializeToString()
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def _depths(spans: list[Any]) -> dict[int, int]:
    """Each span's depth below the entry span(s), for truncation order."""
    parent = {
        s.context.span_id: (s.parent.span_id if s.parent is not None else None)
        for s in spans
    }
    out: dict[int, int] = {}
    for span_id in parent:
        depth, cursor, seen = 0, parent[span_id], set()
        while cursor in parent and cursor not in seen:
            seen.add(cursor)
            depth += 1
            cursor = parent[cursor]
        out[span_id] = depth
    return out


def collect(
    bucket: Any, *, exported_to: list[str] | None = None, limit: int = MAX_ENCODED
) -> dict[str, Any] | None:
    """The `trace` field of an `agent_result`: this request's spans, encoded and
    bounded. None when there is nothing to send.

    Over budget, the deepest and latest-ending spans go first — the entry span and
    the shape near it say most about what happened — and `truncated` counts them."""
    from backend.modules.otel import destinations

    spans = [
        destinations.rebuild(s, attributes=_clean_attrs(s.attributes))
        for s in (bucket.spans if bucket is not None else [])
    ]
    if not spans:
        return None
    truncated = int(getattr(bucket, "dropped", 0) or 0)
    body = _encode(spans)
    if len(body) > limit:
        ids = {s.context.span_id for s in spans}
        # Entry spans hang off something outside the set (the caller's span).
        outside = {
            s.parent.span_id
            for s in spans
            if s.parent is not None and s.parent.span_id not in ids
        }
        depth = _depths(spans)
        # Never drop an entry span (depth 0): it is what the caller attaches to.
        order = sorted(
            (s for s in spans if depth[s.context.span_id] > 0),
            key=lambda s: (depth[s.context.span_id], s.end_time or 0),
            reverse=True,
        )
        step = max(1, len(order) // 16)
        drop: set[int] = set()
        kept = spans
        for start in range(0, len(order), step):
            drop.update(s.context.span_id for s in order[start : start + step])
            kept = _prune([s for s in spans if s.context.span_id not in drop], outside)
            body = _encode(kept) if kept else ""
            if len(body) <= limit:
                break
        truncated += len(spans) - len(kept)
        if not body or len(body) > limit:
            return None
    return {
        "enc": ENCODING,
        "body": body,
        "truncated": truncated,
        "exported_to": list(exported_to or []),
    }


def _prune(spans: list[Any], outside: set[int]) -> list[Any]:
    """Drop spans whose parent was dropped — repeatedly, so a removed subtree
    goes whole. A parent in `outside` (the caller's span) is never missing."""
    out = spans
    while True:
        present = {s.context.span_id for s in out}
        kept = [
            s
            for s in out
            if s.parent is None
            or s.parent.span_id in present
            or s.parent.span_id in outside
        ]
        if len(kept) == len(out):
            return kept
        out = kept


# --- caller: ingest -----------------------------------------------------------------


def _decode(payload: Any) -> list[Any]:
    from backend.modules.otel import decode

    if not isinstance(payload, dict) or payload.get("enc") != ENCODING:
        return []
    body = payload.get("body")
    if not isinstance(body, str) or len(body) > MAX_ENCODED * 2:
        return []
    try:
        raw = decode.inflate(
            base64.b64decode(body, validate=True), "gzip", limit=MAX_INFLATED
        )
        spans = decode.decode(raw, "application/x-protobuf")
    except Exception as exc:  # noqa: BLE001 — a bad payload is just "no spans"
        logger.info("otel: unreadable peer spans (%s)", exc)
        return []
    return spans[:MAX_SPANS]


def _primitive(value: Any) -> Any:
    """An attribute value OTel can carry: primitives and lists of one primitive
    type stay; anything else becomes its JSON text."""
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)) and value:
        kinds = {type(v) for v in value}
        if len(kinds) == 1 and kinds <= {str, bool, int, float}:
            return list(value)
    return json.dumps(value, default=str, ensure_ascii=False)


def _our_span_ids(trace_id: str) -> set[str]:
    """This node's own spans in `trace_id` — ids a returned span may not take."""
    from backend.modules.otel import store

    return {s.span_id for s in store.get_trace(trace_id) if s.origin == "local"}


def accept(spans: list[Any], *, trace_id: str, parent_span_id: str) -> list[Any]:
    """The returned spans worth keeping: our trace, rooted at the span we sent,
    no collisions with our own spans, sane clocks. Pure, for testing."""
    now = time.time_ns()
    ours = _our_span_ids(trace_id)
    candidates = {
        s.span_id: s
        for s in spans
        if s.trace_id == trace_id
        and s.span_id not in ours
        and s.span_id != parent_span_id
        and 0 < s.start_ns <= s.end_ns
        and abs(s.start_ns - now) < _CLOCK_SLACK_NS
    }
    # Keep only what hangs, transitively, off the span we sent.
    kept: dict[str, Any] = {}
    frontier = {parent_span_id}
    while frontier:
        nxt = {
            sid
            for sid, s in candidates.items()
            if sid not in kept and s.parent_span_id in frontier
        }
        for sid in nxt:
            kept[sid] = candidates[sid]
        frontier = nxt
    return list(kept.values())


def ingest(
    payload: Any,
    *,
    sent_traceparent: str | None,
    peer: str,
    reexport: bool | None = None,
) -> int:
    """Store and re-export a peer's returned spans. Returns how many were kept.

    Blocking (sqlite, the encoder) — call it off the event loop."""
    if not sent_traceparent or not payload:
        return 0
    try:
        _, trace_id, parent_span_id, _ = sent_traceparent.split("-")
    except ValueError:
        return 0
    spans = accept(_decode(payload), trace_id=trace_id, parent_span_id=parent_span_id)
    if not spans:
        return 0
    for span in spans:
        span.attrs = {k: _primitive(v) for k, v in _clean_attrs(span.attrs).items()}
        for event in span.events:
            event.attrs = {k: _primitive(v) for k, v in event.attrs.items()}
        span.resource = {**span.resource, "horrible.peer.node_id": peer}
        span.origin = "peer"

    from backend.modules.otel import store, tracing

    store.insert_spans(spans, origin="peer")
    rehydrated = [rehydrate(s) for s in spans]

    # A chain (A asks B, B asks C): C's spans travel on to A inside B's answer.
    bucket = tracing.active_capture()
    if bucket is not None:
        bucket.extend(rehydrated)

    if reexport is None:
        reexport = _setting("otel.exportPeerSpans", True)
    if reexport:
        skip = (
            set(payload.get("exported_to") or [])
            if isinstance(payload, dict)
            else set()
        )
        for name, slot in tracing.export_slots().items():
            inner = slot.inner
            if inner is None or _fingerprint_of(name) in skip:
                continue
            for span in rehydrated:
                try:
                    inner.on_end(span)
                except Exception:  # noqa: BLE001
                    logger.debug("otel: re-export to %s failed", name, exc_info=True)
    return len(spans)


def _fingerprint_of(name: str) -> str | None:
    """This node's fingerprint for destination `name`, when it has one — what a
    callee lists in `exported_to` when it already sent the spans there itself."""
    from backend.modules.otel import destinations

    st = destinations.status(name)
    return st.detail.get("fingerprint") if st is not None else None


def rehydrate(span: Any) -> Any:
    """A stored `models.Span` as an SDK `ReadableSpan`, for the export processors."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import Event, ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import (
        SpanContext,
        SpanKind,
        Status,
        StatusCode,
        TraceFlags,
    )

    flags = TraceFlags(TraceFlags.SAMPLED)
    trace_id = int(span.trace_id, 16)
    parent = (
        SpanContext(
            trace_id, int(span.parent_span_id, 16), is_remote=False, trace_flags=flags
        )
        if span.parent_span_id
        else None
    )
    try:
        kind = SpanKind(max(0, int(span.kind) - 1))
    except ValueError:
        kind = SpanKind.INTERNAL
    try:
        code = StatusCode(int(span.status_code))
    except ValueError:
        code = StatusCode.UNSET
    return ReadableSpan(
        name=span.name,
        context=SpanContext(
            trace_id, int(span.span_id, 16), is_remote=False, trace_flags=flags
        ),
        parent=parent,
        resource=Resource.create(
            {k: _primitive(v) for k, v in (span.resource or {}).items()}
        ),
        attributes=dict(span.attrs),
        events=[
            Event(e.name, attributes=dict(e.attrs), timestamp=int(e.time_ns or 0))
            for e in span.events
        ],
        kind=kind,
        status=Status(code, span.status_message or None)
        if code == StatusCode.ERROR
        else Status(code),
        start_time=int(span.start_ns),
        end_time=int(span.end_ns),
        instrumentation_scope=InstrumentationScope(span.scope or "peer"),
    )
