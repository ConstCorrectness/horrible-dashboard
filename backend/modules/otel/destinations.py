"""Export destinations: one batch exporter per place the node's spans go.

The generic OTLP connector (`export.py`) and the Opik connector (`opik.py`) are
each a destination with its own slot on the private provider
(`tracing.export_slot(name)`), its own content decision, and an optional
`transform` that rewrites spans for that backend alone. The local store and every
other destination keep seeing the spans exactly as they were made.

**Every export is recorded.** The SDK's exporter logs a failure and moves on, so a
revoked key looks exactly like a quiet node. `recording_session` hooks the
exporter's own `requests.Session` (a public constructor argument) and keeps the
last status and reason — never the headers, which carry the key. `statuses()` is
what `GET /api/otel/export` and the connectors' `error` field read.

Attach and detach are safe to call repeatedly: the previous processor is flushed
and shut down on a thread, so a collector that is down cannot stall the caller.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

logger = logging.getLogger("otel")

#: The exporter's per-request timeout. Short, because shutting a destination down
#: flushes it, and a flush to a dead collector waits this long per batch.
EXPORT_TIMEOUT_S = 5

_KEEP: Any = object()
#: Pass as a `rebuild` argument to leave that field as it was.
KEEP: Any = _KEEP


@dataclass
class ExportStatus:
    name: str
    label: str = ""
    host: str = ""
    include_content: bool = False
    #: Free-form, non-secret facts about the destination (Opik's project).
    detail: dict[str, str] = field(default_factory=dict)
    last_ok_at: float | None = None
    last_error: str = ""
    last_error_at: float | None = None
    spans_ok: int = 0
    spans_failed: int = 0

    @property
    def failing(self) -> bool:
        """Whether the most recent outcome was a failure."""
        if self.last_error_at is None:
            return False
        return self.last_ok_at is None or self.last_error_at > self.last_ok_at


_status: dict[str, ExportStatus] = {}
_status_lock = threading.Lock()
_last_http = threading.local()


def status(name: str) -> ExportStatus | None:
    return _status.get(name)


#: Each destination's content decision, read live: the generic exporter's follows
#: a setting that can change without the destination being reattached.
_content_fns: dict[str, Callable[[], bool]] = {}


def statuses() -> list[dict[str, Any]]:
    """Every attached destination, for the UI. Nothing here is a secret."""
    with _status_lock:
        out = []
        for s in _status.values():
            fn = _content_fns.get(s.name)
            if fn is not None:
                try:
                    s.include_content = bool(fn())
                except Exception:  # noqa: BLE001
                    pass
            out.append({**asdict(s), "failing": s.failing})
        return out


def recording_session(name: str) -> Any:
    """A `requests.Session` for the OTLP exporter that remembers each response's
    status. Headers are never read: they hold the destination's credential."""
    import requests

    session = requests.Session()

    def hook(response: Any, *args: Any, **kwargs: Any) -> None:
        try:
            _last_http.value = (int(response.status_code), str(response.reason or ""))
        except Exception:  # noqa: BLE001
            pass

    session.hooks["response"].append(hook)
    return session


def rebuild(
    span: Any,
    *,
    attributes: Any = _KEEP,
    parent: Any = _KEEP,
    resource: Any = _KEEP,
) -> Any:
    """A copy of a finished span with some fields replaced. `ReadableSpan` is
    immutable once ended; a new one is the supported way to change what ships."""
    from opentelemetry.sdk.trace import ReadableSpan

    return ReadableSpan(
        name=span.name,
        context=span.get_span_context(),
        parent=span.parent if parent is _KEEP else parent,
        resource=span.resource if resource is _KEEP else resource,
        attributes=dict(span.attributes or {}) if attributes is _KEEP else attributes,
        events=span.events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def _make_exporter_cls() -> Any:
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class DestinationExporter(SpanExporter):
        """Strips content unless the destination asked, applies its transform,
        delegates to the OTLP exporter, and records the outcome."""

        def __init__(
            self,
            name: str,
            inner: Any,
            transform: Callable[[Any], Any] | None,
            include_content: Callable[[], bool],
        ) -> None:
            self.name = name
            self.inner = inner
            self.transform = transform
            self.include_content = include_content

        def _prepare(self, spans: Any) -> list[Any]:
            from backend.modules.otel import tracing

            keep = self.include_content()
            out = []
            for span in spans:
                try:
                    if not keep and any(
                        k in tracing.CONTENT_ATTRS for k in (span.attributes or {})
                    ):
                        span = rebuild(
                            span, attributes=tracing.strip_content(span.attributes)
                        )
                    if self.transform is not None:
                        span = self.transform(span)
                except Exception:  # noqa: BLE001 — a bad span must not sink a batch
                    logger.debug("otel: %s transform failed", self.name, exc_info=True)
                    continue
                if span is not None:
                    out.append(span)
            return out

        def export(self, spans: Any) -> Any:
            prepared = self._prepare(spans)
            if not prepared:
                return SpanExportResult.SUCCESS
            _last_http.value = None
            try:
                result = self.inner.export(prepared)
            except Exception as exc:  # noqa: BLE001
                logger.info("otel: export to %s raised: %s", self.name, exc)
                result = SpanExportResult.FAILURE
            _record(self.name, result == SpanExportResult.SUCCESS, len(prepared))
            return result

        def shutdown(self) -> None:
            self.inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return self.inner.force_flush(timeout_millis)

    return DestinationExporter


_exporter_cls: Any = None


def _record(name: str, ok: bool, count: int) -> None:
    http = getattr(_last_http, "value", None)
    with _status_lock:
        st = _status.get(name)
        if st is None:
            return
        now = time.time()
        if ok:
            st.last_ok_at = now
            st.spans_ok += count
            return
        st.spans_failed += count
        st.last_error_at = now
        if http is None:
            st.last_error = "Could not reach the collector."
        else:
            code, reason = http
            st.last_error = f"HTTP {code} {reason}".strip()


def attach(
    name: str,
    *,
    url: str | None,
    headers: dict[str, str] | None = None,
    transform: Callable[[Any], Any] | None = None,
    include_content: bool | Callable[[], bool] = False,
    label: str = "",
    detail: dict[str, str] | None = None,
) -> bool:
    """(Re)attach destination `name`, or detach it when `url` is None.

    `include_content` is either a fixed choice (registered with the tracer, so
    content is recorded for this destination) or a callable read at export time —
    the generic exporter's, which follows the global setting and so needs no
    registration of its own.
    """
    global _exporter_cls
    from urllib.parse import urlparse

    from backend.modules.otel import tracing

    slot = tracing.export_slot(name)
    old = slot.inner
    slot.inner = None
    if old is not None:
        threading.Thread(target=old.shutdown, daemon=True).start()
    if url is None:
        tracing.set_destination_content(name, False)
        with _status_lock:
            _status.pop(name, None)
            _content_fns.pop(name, None)
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        if _exporter_cls is None:
            _exporter_cls = _make_exporter_cls()
        wants = include_content if callable(include_content) else None
        fixed = bool(include_content) if wants is None else False
        exporter = _exporter_cls(
            name,
            OTLPSpanExporter(
                endpoint=url,
                headers=headers or {},
                timeout=EXPORT_TIMEOUT_S,
                session=recording_session(name),
            ),
            transform,
            wants or (lambda: fixed),
        )
        with _status_lock:
            _status[name] = ExportStatus(
                name=name,
                label=label,
                host=urlparse(url).hostname or "",
                include_content=fixed if wants is None else bool(wants()),
                detail=dict(detail or {}),
            )
            if wants is not None:
                _content_fns[name] = wants
            else:
                _content_fns.pop(name, None)
        tracing.set_destination_content(name, fixed)
        slot.inner = BatchSpanProcessor(exporter)
    except Exception:  # noqa: BLE001
        logger.warning(
            "otel: could not attach %s exporter for %s", name, url, exc_info=True
        )
        tracing.set_destination_content(name, False)
        with _status_lock:
            _status.pop(name, None)
        return False
    return True


def detach(name: str) -> None:
    attach(name, url=None)


def fingerprint(*parts: str) -> str:
    """A short, stable id for a destination, safe to send to a peer: it says
    "same Opik project as you" without naming the workspace."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
