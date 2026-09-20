"""External export: configuration precedence, the self-loop refusal, forwarding."""

from __future__ import annotations

import pytest

from backend.modules.otel import decode, export, ids, tracing


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    tracing.export_slot().inner = None


def test_traces_url_and_headers_follow_the_otel_env_format() -> None:
    assert export.traces_url("http://h:4318") == "http://h:4318/v1/traces"
    assert export.traces_url("http://h:4318/") == "http://h:4318/v1/traces"
    assert export.traces_url("http://h/v1/traces") == "http://h/v1/traces"
    assert export.parse_headers("Authorization=Bearer%20abc,x-team = t1 ,junk") == {
        "Authorization": "Bearer abc",
        "x-team": "t1",
    }


def test_environment_wins_over_the_stored_connector(monkeypatch) -> None:
    export._put(export._ENDPOINT, "http://stored:4318")
    assert export.config() == ("http://stored:4318/v1/traces", {})
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://env:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "k=v")
    assert export.config() == ("http://env:4318/v1/traces", {"k": "v"})


@pytest.mark.anyio
async def test_connector_stores_secrets_and_attaches_an_exporter() -> None:
    connector = export.build()
    assert connector.status().connected is False
    step = await connector.begin({})
    assert all(f["secret"] and f["value"] == "" for f in step["fields"])

    result = await connector.submit(
        {"endpoint": "http://collector:4318", "headers": "x-api-key=s3cret"}
    )
    assert result["connected"] is True
    assert "s3cret" not in str(result)
    assert tracing.export_slot().inner is not None

    # Blank means keep, never clear.
    assert (await connector.submit({"endpoint": "", "headers": ""}))["connected"]
    assert export.config() == (
        "http://collector:4318/v1/traces",
        {"x-api-key": "s3cret"},
    )

    await connector.disconnect()
    assert export.config() is None
    assert tracing.export_slot().inner is None


@pytest.mark.anyio
async def test_exporting_to_our_own_receiver_is_refused() -> None:
    result = await export.build().submit({"endpoint": "http://127.0.0.1:8000/api/otel"})
    assert "error" in result
    assert export.config() is None


@pytest.mark.anyio
async def test_forward_received_relays_the_original_bytes(monkeypatch) -> None:
    sent: list[tuple[str, bytes, dict]] = []

    class FakeClient:
        def __init__(self, **_kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def post(self, url, content, headers):
            sent.append((url, content, headers))

            class R:
                status_code = 200

            return R()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-api-key=k")

    monkeypatch.setattr(export, "_forward_enabled", lambda: False)
    await export.forward_received(b"\x01\x02", "application/x-protobuf", None)
    assert sent == []

    monkeypatch.setattr(export, "_forward_enabled", lambda: True)
    await export.forward_received(b"\x01\x02", "application/x-protobuf", "gzip")
    ((url, body, headers),) = sent
    assert url == "http://collector:4318/v1/traces"
    assert body == b"\x01\x02"
    assert headers["x-api-key"] == "k"
    assert headers["content-encoding"] == "gzip"


def _collector():
    """A real OTLP/HTTP collector on a free port, in a thread.

    The exporter wiring — provider slot, batch processor, endpoint suffix, headers —
    is the half a unit test cannot reach: everything up to `BatchSpanProcessor` can be
    right while nothing ever leaves the process.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: list[tuple[str, dict[str, str], bytes]] = []

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
            self.send_response(200)
            self.send_header("content-type", "application/x-protobuf")
            self.end_headers()
            self.wfile.write(b"")

        def log_message(self, *args):  # keep the suite's output clean
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, received


def test_the_nodes_spans_actually_reach_a_collector(monkeypatch) -> None:
    server, received = _collector()
    host, port = server.server_address[0], server.server_address[1]
    try:
        monkeypatch.setattr(tracing, "enabled", lambda: True)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://{host}:{port}")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-api-key=k3y")
        assert export.configure() is True

        with tracing.agent_span(
            turn_id="exported",
            agent_id="main",
            agent_name="Main",
            model="m",
            provider="ollama",
        ):
            pass
        tracing.force_flush()

        assert received, "the exporter never sent anything"
        path, headers, body = received[-1]
        # The endpoint is a base URL; the exporter appends the signal's path itself.
        assert path == "/v1/traces"
        assert headers["x-api-key"] == "k3y"
        spans = decode.decode(
            decode.inflate(body, headers.get("content-encoding")),
            "application/x-protobuf",
        )
        assert [s.name for s in spans] == ["invoke_agent Main"]
        assert spans[0].attrs["horrible.turn_id"] == "exported"
        assert spans[0].trace_id == ids.trace_id_for_turn("exported")
    finally:
        tracing.export_slot().inner = None
        server.shutdown()
        server.server_close()
