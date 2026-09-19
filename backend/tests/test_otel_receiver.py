"""The OTLP/HTTP receiver end to end: real SDK-encoded protobuf in, runs out.

The protobuf leg is encoded by the OpenTelemetry SDK's own OTLP encoder rather
than by hand, so the test fails if our decoder and the exporter real agents use
ever disagree about the wire.
"""

from __future__ import annotations

import contextlib
import gzip
import json

import pytest
from fastapi.testclient import TestClient

from backend.modules.otel import auth, materialize, store
from backend.modules.trajectories import store as traj_store

LOOPBACK = ("127.0.0.1", 50000)
REMOTE = ("10.0.0.7", 50000)


@pytest.fixture(autouse=True)
def _reset_otel():
    materialize.reset()
    yield
    materialize.reset()


def _client(addr=LOOPBACK) -> contextlib.nullcontext[TestClient]:
    """Deliberately *not* entering the TestClient: that would run the app lifespan,
    start the debounce worker, and make every assertion race it. Without a worker,
    `materialize.touch` projects inline."""
    from backend.app import app

    return contextlib.nullcontext(TestClient(app, client=addr))


def _sdk_protobuf(n_children: int = 2, *, with_root: bool = True) -> tuple[bytes, str]:
    """Encode a small agent trace with the real SDK. Returns (body, trace_id)."""
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": "test-agent", "horrible.dataset": "nb"}
        )
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span(
        "invoke_agent helper",
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "helper",
        },
    ) as root:
        for i in range(n_children):
            with tracer.start_as_current_span(
                "execute_tool lookup",
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": "lookup",
                    "gen_ai.tool.call.arguments": json.dumps({"i": i}),
                },
            ):
                pass
        trace_id = format(root.get_span_context().trace_id, "032x")
    spans = list(exporter.get_finished_spans())
    if not with_root:
        spans = [s for s in spans if s.parent is not None]
    return encode_spans(spans).SerializeToString(), trace_id


def _run_for(trace_id: str):
    with traj_store.get_db_conn() as conn:
        row = conn.execute(
            "SELECT id FROM traj_runs WHERE external_id = ?", (trace_id,)
        ).fetchone()
    return traj_store.get_run(row["id"]) if row else None


def _post(client: TestClient, body: bytes, **headers) -> int:
    headers.setdefault("content-type", "application/x-protobuf")
    return client.post("/api/otel/v1/traces", content=body, headers=headers).status_code


def test_protobuf_from_the_sdk_projects_a_run() -> None:
    body, trace_id = _sdk_protobuf()
    with _client() as client:
        response = client.post(
            "/api/otel/v1/traces",
            content=body,
            headers={"content-type": "application/x-protobuf"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-protobuf")
    run = _run_for(trace_id)
    assert run is not None
    assert run.dataset_id == "nb"
    assert run.agent_name == "helper"
    assert run.status == "complete"
    assert [s.name for s in run.step_list] == ["lookup", "lookup"]
    # Order among same-tick siblings is clock-dependent; content is not.
    assert sorted(s.args["i"] for s in run.step_list) == [0, 1]


def test_gzip_and_json_encodings() -> None:
    doc = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "js-agent"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "manual"},
                        "spans": [
                            {
                                "traceId": "5b8efff798038103d269b633813fc60c",
                                "spanId": "eee19b7ec3c1b174",
                                "name": "invoke_agent js",
                                "kind": "SPAN_KIND_INTERNAL",
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000001000000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.operation.name",
                                        "value": {"stringValue": "invoke_agent"},
                                    },
                                    {
                                        "key": "gen_ai.usage.input_tokens",
                                        "value": {"intValue": "12"},
                                    },
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }
    with _client() as client:
        response = client.post(
            "/api/otel/v1/traces",
            content=gzip.compress(json.dumps(doc).encode()),
            headers={"content-type": "application/json", "content-encoding": "gzip"},
        )
    assert response.status_code == 200 and response.json() == {}
    spans = store.get_trace("5b8efff798038103d269b633813fc60c")
    assert len(spans) == 1
    assert spans[0].span_id == "eee19b7ec3c1b174"  # hex kept as hex, not base64'd
    assert spans[0].attrs["gen_ai.usage.input_tokens"] == 12
    assert spans[0].resource["service.name"] == "js-agent"


def test_children_first_then_root_and_idempotent_resend() -> None:
    children, trace_id = _sdk_protobuf(with_root=False)
    with _client() as client:
        assert _post(client, children) == 200
        run = _run_for(trace_id)
        assert run is not None and run.status == "running"
        assert len(run.step_list) == 2

        full, trace_id_2 = _sdk_protobuf()
        # A fresh trace from the SDK — replay it twice to prove a retried batch
        # does not double anything.
        assert _post(client, full) == 200
        assert _post(client, full) == 200
    run2 = _run_for(trace_id_2)
    assert run2 is not None and run2.status == "complete"
    assert len(run2.step_list) == 2
    assert len(store.get_trace(trace_id_2)) == 3


def test_remote_client_needs_the_token() -> None:
    body, _ = _sdk_protobuf()
    token = auth.get_token()
    with _client(REMOTE) as client:
        assert _post(client, body) == 401
        assert _post(client, body, authorization="Bearer nope") == 401
        assert _post(client, body, authorization=f"Bearer {token}") == 200
        # …and cannot read the token either.
        assert client.get("/api/otel/ingest").json()["token"] is None
        assert client.post("/api/otel/ingest/rotate").status_code == 403
    with _client() as client:
        assert client.get("/api/otel/ingest").json()["token"] == token


def test_forwarded_header_only_trusted_from_a_loopback_peer() -> None:
    body, _ = _sdk_protobuf()
    # Relayed by the (loopback) Vite proxy from a LAN client: not loopback.
    with _client() as client:
        assert _post(client, body, **{"x-forwarded-for": "10.0.0.7"}) == 401
        # The proxy appends the address it saw; a client-supplied first entry
        # claiming loopback changes nothing.
        assert _post(client, body, **{"x-forwarded-for": "127.0.0.1, 10.0.0.7"}) == 401
        assert _post(client, body, **{"x-forwarded-for": "::ffff:127.0.0.1"}) == 200
    # A remote peer cannot talk its way to loopback with the header.
    with _client(REMOTE) as client:
        assert _post(client, body, **{"x-forwarded-for": "127.0.0.1"}) == 401


def test_authorization_header_is_blanked_in_telemetry() -> None:
    from backend.modules.telemetry.recorder import recorder

    body, _ = _sdk_protobuf()
    token = auth.get_token()
    recorder.clear()
    with _client(REMOTE) as client:
        assert _post(client, body, authorization=f"Bearer {token}") == 200
    events = [e for e in recorder.recent() if e.target == "/api/otel/v1/traces"]
    assert events, "the inbound request should still be observed"
    for event in events:
        headers = {k.lower(): v for k, v in (event.request_headers or {}).items()}
        assert token not in json.dumps(headers)
        assert headers.get("authorization") == "***"
        assert event.request_body in (None, "")


def test_bad_bodies_are_400() -> None:
    with _client() as client:
        assert _post(client, b"\xff\x00garbage") == 400
        assert (
            client.post(
                "/api/otel/v1/traces",
                content=b"[]",
                headers={"content-type": "application/json"},
            ).status_code
            == 400
        )
        assert _post(client, b"x", **{"content-encoding": "br"}) == 400
