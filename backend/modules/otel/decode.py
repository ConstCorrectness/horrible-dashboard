"""OTLP/HTTP trace payloads → `Span`s.

Both encodings the spec defines are accepted, because both are what real exporters
send: the Python SDK defaults to `http/protobuf`, while the JS SDK and a curl-driven
test send JSON. They are **not** the same document under two serializations:

- OTLP/JSON encodes `traceId`/`spanId` as **hex**, where protobuf's own JSON mapping
  would use base64 for a `bytes` field. Feeding OTLP/JSON to `json_format.Parse`
  therefore corrupts every id — silently, since base64 of hex text is still valid.
  So the JSON leg is walked by hand, and base64 is tolerated only as a fallback for
  senders that got this wrong.
- 64-bit integers (`startTimeUnixNano`, `intValue`) arrive as JSON *strings*.

`gzip` is honoured (`OTEL_EXPORTER_OTLP_COMPRESSION=gzip`), with a ceiling on the
inflated size: a 1 MB body that inflates to 4 GB is a denial of service with a
content-type.
"""

from __future__ import annotations

import base64
import binascii
import json
import zlib
from typing import Any

from backend.modules.otel.models import Span, SpanEvent

#: Largest inflated body accepted. Generous for real batches (the SDK's default
#: batch is 512 spans) and still bounded.
MAX_INFLATED_BYTES = 64 * 1024 * 1024


class DecodeError(ValueError):
    """The body is not a trace export this endpoint understands (→ HTTP 400)."""


def inflate(body: bytes, encoding: str | None) -> bytes:
    if not encoding or encoding.lower() in ("identity", ""):
        return body
    if encoding.lower() != "gzip":
        raise DecodeError(f"unsupported Content-Encoding: {encoding}")
    inflater = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(body, MAX_INFLATED_BYTES + 1)
    except zlib.error as exc:
        raise DecodeError(f"bad gzip body: {exc}") from exc
    if len(out) > MAX_INFLATED_BYTES or inflater.unconsumed_tail:
        raise DecodeError("inflated body exceeds the size limit")
    return out


def decode(body: bytes, content_type: str | None) -> list[Span]:
    kind = (content_type or "").split(";", 1)[0].strip().lower()
    if kind in ("application/json", "text/json"):
        try:
            doc = json.loads(body or b"{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise DecodeError(f"bad JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise DecodeError("expected a JSON object")
        return _from_json(doc)
    if kind in ("application/x-protobuf", "application/protobuf", ""):
        return _from_protobuf(body)
    raise DecodeError(f"unsupported Content-Type: {content_type}")


# --- protobuf ----------------------------------------------------------------


def _pb_value(value: Any) -> Any:
    which = value.WhichOneof("value")
    if which is None:
        return None
    if which == "array_value":
        return [_pb_value(v) for v in value.array_value.values]
    if which == "kvlist_value":
        return {kv.key: _pb_value(kv.value) for kv in value.kvlist_value.values}
    if which == "bytes_value":
        return value.bytes_value.hex()
    return getattr(value, which)


def _pb_attrs(items: Any) -> dict[str, Any]:
    return {kv.key: _pb_value(kv.value) for kv in items}


def _from_protobuf(body: bytes) -> list[Span]:
    from google.protobuf.message import DecodeError as PbDecodeError
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(body)
    except PbDecodeError as exc:
        raise DecodeError(f"bad protobuf body: {exc}") from exc

    spans: list[Span] = []
    for rs in request.resource_spans:
        resource = _pb_attrs(rs.resource.attributes)
        for ss in rs.scope_spans:
            scope = ss.scope.name
            for s in ss.spans:
                if not s.trace_id or not s.span_id:
                    continue
                spans.append(
                    Span(
                        trace_id=s.trace_id.hex(),
                        span_id=s.span_id.hex(),
                        parent_span_id=s.parent_span_id.hex(),
                        name=s.name,
                        kind=int(s.kind),
                        start_ns=int(s.start_time_unix_nano),
                        end_ns=int(s.end_time_unix_nano),
                        status_code=int(s.status.code),
                        status_message=s.status.message,
                        attrs=_pb_attrs(s.attributes),
                        events=[
                            SpanEvent(
                                name=e.name,
                                time_ns=int(e.time_unix_nano),
                                attrs=_pb_attrs(e.attributes),
                            )
                            for e in s.events
                        ],
                        resource=resource,
                        scope=scope,
                    )
                )
    return spans


# --- JSON --------------------------------------------------------------------


def _get(d: dict[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    if camel in d:
        return d[camel]
    return d.get(snake, default)


def _json_id(raw: Any, width: int) -> str:
    """Hex per the OTLP/JSON spec; base64 accepted from senders that used
    protobuf's generic JSON mapping instead."""
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.strip()
    if len(text) == width * 2:
        try:
            bytes.fromhex(text)
            return text.lower()
        except ValueError:
            pass
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return ""
    return decoded.hex() if len(decoded) == width else ""


def _json_int(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


_ENUMS = {
    "STATUS_CODE_UNSET": 0,
    "STATUS_CODE_OK": 1,
    "STATUS_CODE_ERROR": 2,
    "SPAN_KIND_UNSPECIFIED": 0,
    "SPAN_KIND_INTERNAL": 1,
    "SPAN_KIND_SERVER": 2,
    "SPAN_KIND_CLIENT": 3,
    "SPAN_KIND_PRODUCER": 4,
    "SPAN_KIND_CONSUMER": 5,
}


def _json_enum(raw: Any) -> int:
    """The spec says integers; protobuf-JSON writers emit the enum *name*."""
    if isinstance(raw, str) and raw in _ENUMS:
        return _ENUMS[raw]
    return _json_int(raw)


def _json_value(v: Any) -> Any:
    if not isinstance(v, dict):
        return v
    for camel, snake in (
        ("stringValue", "string_value"),
        ("boolValue", "bool_value"),
        ("doubleValue", "double_value"),
    ):
        if camel in v or snake in v:
            return _get(v, camel, snake)
    if "intValue" in v or "int_value" in v:
        return _json_int(_get(v, "intValue", "int_value"))
    if "arrayValue" in v or "array_value" in v:
        arr = _get(v, "arrayValue", "array_value") or {}
        return [_json_value(x) for x in arr.get("values", [])]
    if "kvlistValue" in v or "kvlist_value" in v:
        kv = _get(v, "kvlistValue", "kvlist_value") or {}
        return _json_attrs(kv.get("values", []))
    if "bytesValue" in v or "bytes_value" in v:
        return _get(v, "bytesValue", "bytes_value")
    return None


def _json_attrs(items: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kv in items or []:
        if isinstance(kv, dict) and "key" in kv:
            out[str(kv["key"])] = _json_value(kv.get("value"))
    return out


def _from_json(doc: dict[str, Any]) -> list[Span]:
    spans: list[Span] = []
    for rs in _get(doc, "resourceSpans", "resource_spans", []) or []:
        resource = _json_attrs((rs.get("resource") or {}).get("attributes"))
        for ss in _get(rs, "scopeSpans", "scope_spans", []) or []:
            scope = str((ss.get("scope") or {}).get("name") or "")
            for s in ss.get("spans") or []:
                trace_id = _json_id(_get(s, "traceId", "trace_id"), 16)
                span_id = _json_id(_get(s, "spanId", "span_id"), 8)
                if not trace_id or not span_id:
                    continue
                status = s.get("status") or {}
                spans.append(
                    Span(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=_json_id(
                            _get(s, "parentSpanId", "parent_span_id"), 8
                        ),
                        name=str(s.get("name") or ""),
                        kind=_json_enum(s.get("kind")),
                        start_ns=_json_int(
                            _get(s, "startTimeUnixNano", "start_time_unix_nano")
                        ),
                        end_ns=_json_int(
                            _get(s, "endTimeUnixNano", "end_time_unix_nano")
                        ),
                        status_code=_json_enum(status.get("code")),
                        status_message=str(status.get("message") or ""),
                        attrs=_json_attrs(s.get("attributes")),
                        events=[
                            SpanEvent(
                                name=str(e.get("name") or ""),
                                time_ns=_json_int(
                                    _get(e, "timeUnixNano", "time_unix_nano")
                                ),
                                attrs=_json_attrs(e.get("attributes")),
                            )
                            for e in s.get("events") or []
                            if isinstance(e, dict)
                        ],
                        resource=resource,
                        scope=scope,
                    )
                )
    return spans
