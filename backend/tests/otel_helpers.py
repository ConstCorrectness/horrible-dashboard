"""Shared fixtures for the OTLP export tests."""

from __future__ import annotations

import contextlib
import json
from typing import Any, Iterator


@contextlib.contextmanager
def captured_spans(monkeypatch) -> Iterator[Any]:
    """Finished spans, captured synchronously through the generic export slot.

    Yields an `InMemorySpanExporter`; read `.get_finished_spans()`."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from backend.modules.otel import materialize, tracing

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    exporter = InMemorySpanExporter()
    slot = tracing.export_slot()
    slot.inner = SimpleSpanProcessor(exporter)
    materialize.reset()
    try:
        yield exporter
    finally:
        slot.inner = None
        materialize.reset()


def collector(
    *,
    post_status: int = 200,
    get_routes: dict[str, tuple[int, Any]] | None = None,
):
    """A real OTLP/HTTP collector on a free port, in a thread.

    The exporter wiring — provider slot, batch processor, endpoint suffix, headers —
    is the half a unit test cannot reach: everything up to `BatchSpanProcessor` can be
    right while nothing ever leaves the process.

    `post_status` is what every export gets back (a 401 to test failure recording).
    `get_routes` maps a GET path (query string ignored) to `(status, json body)`, so
    the same server can stand in for Opik's projects and health endpoints. Each
    GET is recorded too, as `("GET " + path, headers, b"")`.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: list[tuple[str, dict[str, str], bytes]] = []
    routes = dict(get_routes or {})

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length") or 0)
            received.append(
                (
                    self.path,
                    {k.lower(): v for k, v in self.headers.items()},
                    self.rfile.read(length),
                )
            )
            self.send_response(post_status)
            self.send_header("content-type", "application/x-protobuf")
            self.end_headers()
            self.wfile.write(b"")

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            received.append(
                (f"GET {path}", {k.lower(): v for k, v in self.headers.items()}, b"")
            )
            status, body = routes.get(path, (404, {"detail": "Not Found"}))
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # keep the suite's output clean
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, received
