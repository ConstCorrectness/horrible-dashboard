"""OTLP/gRPC receiver — the other half of what exporters expect, opt-in.

The OTel spec's **default** protocol is gRPC, so an agent whose owner never set
`OTEL_EXPORTER_OTLP_PROTOCOL` talks to :4317 and finds nothing. That failure is at
least loud (connection refused), which is why HTTP came first, but "any exporter
works" is the point of being a collector at all.

It is **off unless `otel.grpcPort` is set**, because this binds a *second* listening
port, and a module that quietly opens one on every node is not something to discover
later in `netstat`. For the same reason it binds `otel.grpcHost` (default loopback):
the node's own HTTP port may be loopback-only, and opening a LAN port from an
unrelated setting would widen the node's exposure without saying so.

Auth is the HTTP rule, unchanged: **loopback needs nothing, anything else needs the
ingest token** — here as the `authorization` metadata entry, which is where a gRPC
exporter puts `OTEL_EXPORTER_OTLP_HEADERS`.

The port is read once, at startup. Changing the setting takes a restart, and the docs
say so rather than pretending a live rebind exists.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from backend.modules.otel import auth, decode, materialize, store

logger = logging.getLogger("otel")

DEFAULT_PORT = 4317

_server: Any = None
_bound: int | None = None


def configured() -> tuple[str, int]:
    from backend.modules.settings import get_value

    try:
        port = int(get_value("otel.grpcPort", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    host = str(get_value("otel.grpcHost", "127.0.0.1") or "127.0.0.1")
    return host, port


def bound_port() -> int | None:
    """The port actually listening, or None. Not the setting: a port that was
    already taken must not be reported as though it were serving."""
    return _bound


def _peer_host(peer: str) -> str:
    """gRPC peer strings are `ipv4:127.0.0.1:53201` / `ipv6:[::1]:53201`."""
    if peer.startswith("ipv6:"):
        rest = peer[5:]
        return rest[1 : rest.rindex("]")] if "]" in rest else rest
    if peer.startswith("ipv4:"):
        return peer[5:].rsplit(":", 1)[0]
    return peer.rsplit(":", 1)[0]


def authorized(peer: str, metadata: Any) -> bool:
    if auth.is_loopback_addr(_peer_host(peer)):
        return True
    supplied = ""
    for key, value in metadata or ():
        if str(key).lower() == "authorization":
            scheme, _, rest = str(value).partition(" ")
            supplied = rest.strip() if scheme.lower() == "bearer" else ""
    expected = auth.get_token(create=False)
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))


def _servicer_class() -> Any:
    import grpc
    from opentelemetry.proto.collector.trace.v1 import (
        trace_service_pb2,
        trace_service_pb2_grpc,
    )

    class TraceService(trace_service_pb2_grpc.TraceServiceServicer):
        async def Export(self, request: Any, context: Any) -> Any:  # noqa: N802
            if not authorized(context.peer(), context.invocation_metadata()):
                await context.abort(
                    grpc.StatusCode.UNAUTHENTICATED,
                    "OTLP ingest from a non-loopback address needs the ingest token",
                )
            spans = decode.spans_from_request(request)
            # Straight onto the loop's default executor: the store is sqlite and the
            # projection is a debounce, neither of which belongs in a gRPC handler.
            import asyncio

            touched = await asyncio.to_thread(
                store.insert_spans, spans, origin="received"
            )
            await asyncio.to_thread(materialize.touch, touched)
            return trace_service_pb2.ExportTraceServiceResponse()

    return TraceService


async def start() -> int | None:
    """Bind and serve, when configured. Returns the port, or None."""
    global _server, _bound
    host, port = configured()
    if port <= 0:
        return None
    try:
        import grpc
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc

        server = grpc.aio.server()
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
            _servicer_class()(), server
        )
        # `add_insecure_port` returns 0 when the port is taken — it does not raise,
        # so a node whose 4317 is already occupied would otherwise report success and
        # serve nothing.
        bound = server.add_insecure_port(f"{host}:{port}")
        if not bound:
            logger.warning("otel: gRPC port %s:%s is unavailable", host, port)
            return None
        await server.start()
    except Exception:  # noqa: BLE001 — a receiver that will not start must not stop boot
        logger.warning("otel: could not start the gRPC receiver", exc_info=True)
        return None
    _server, _bound = server, bound
    logger.info("otel: OTLP/gRPC receiver on %s:%s", host, bound)
    return bound


async def stop() -> None:
    global _server, _bound
    server, _server, _bound = _server, None, None
    if server is not None:
        try:
            await server.stop(grace=1.0)
        except Exception:  # noqa: BLE001
            logger.debug("otel: gRPC shutdown failed", exc_info=True)
