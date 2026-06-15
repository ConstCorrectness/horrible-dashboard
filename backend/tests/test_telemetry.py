import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.telemetry.instrument import (
    _MAX_BODY_CHARS,
    REDACTED,
    _max_body_chars,
    instrumented_client,
    safe_body,
    tee_stream,
)
from backend.modules.telemetry.recorder import recorder


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    recorder.clear()
    return TestClient(app)


def test_inbound_request_is_recorded(client: TestClient) -> None:
    client.get("/api/health")
    events = client.get("/api/telemetry/recent").json()
    health = [e for e in events if e["target"] == "/api/health"]
    assert health, "inbound /api/health should be recorded"
    assert health[-1]["source"] == "inbound"
    assert health[-1]["method"] == "GET"
    assert health[-1]["status"] == 200


def test_recorder_does_not_observe_itself(client: TestClient) -> None:
    client.get("/api/telemetry/recent")
    events = client.get("/api/telemetry/recent").json()
    assert not any(e["target"].startswith("/api/telemetry") for e in events)


def test_inbound_detail_is_captured(client: TestClient) -> None:
    client.put("/api/dashboard/layout", json={"widgets": ["dashboard.welcome"]})
    events = client.get("/api/telemetry/recent").json()
    put = [e for e in events if e["target"] == "/api/dashboard/layout"][-1]
    assert "widgets" in put["request_body"]
    assert put["request_headers"]["content-type"] == "application/json"
    assert "content-type" in put["response_headers"]


def test_credential_headers_are_redacted(client: TestClient) -> None:
    client.get(
        "/api/health", headers={"Authorization": "Bearer hunter2", "X-Api-Key": "k"}
    )
    events = client.get("/api/telemetry/recent").json()
    health = [e for e in events if e["target"] == "/api/health"][-1]
    assert health["request_headers"]["authorization"] == REDACTED
    assert health["request_headers"]["x-api-key"] == REDACTED
    assert "hunter2" not in str(events)


def test_sensitive_route_bodies_are_suppressed(client: TestClient) -> None:
    # Clubhouse bodies carry phone numbers and SMS codes — never recorded. The
    # middleware redacts by path prefix, so an unrouted path exercises it
    # without reaching the real Clubhouse API.
    client.post("/api/clubhouse/does-not-exist", json={"phone_number": "+15551234567"})
    events = client.get("/api/telemetry/recent").json()
    auth = [e for e in events if e["target"].startswith("/api/clubhouse")][-1]
    assert "+15551234567" not in str(events)
    assert auth["request_body"] == "[redacted — sensitive route]"


def test_outbound_call_is_recorded() -> None:
    recorder.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async def go() -> None:
        async with instrumented_client(
            transport=httpx.MockTransport(handler), base_url="https://clubhouse.test"
        ) as client:
            await client.post(
                "/start_phone_number_auth",
                json={"phone_number": "+15551234567"},
                headers={"Authorization": "Bearer ch-token"},
            )

    asyncio.run(go())

    outbound = [e for e in recorder.recent() if e.source == "outbound"]
    assert outbound, "outbound call should be recorded"
    last = outbound[-1]
    assert last.method == "POST"
    assert last.target == "https://clubhouse.test/start_phone_number_auth"
    assert last.status == 200
    assert last.duration_ms is not None
    # Detail is captured, but the clubhouse host is sensitive: body suppressed,
    # credential headers masked.
    assert last.request_headers is not None
    assert last.request_headers["authorization"] == REDACTED
    assert last.request_body == "[redacted — sensitive route]"
    assert "+15551234567" not in last.model_dump_json()


def test_outbound_response_body_is_captured() -> None:
    # Non-streaming responses (the agent's stream:false /api/chat round) are
    # captured so request↔response pairs are visible end to end.
    recorder.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "hello there"}})

    async def go() -> None:
        async with instrumented_client(
            transport=httpx.MockTransport(handler), base_url="http://localhost:11434"
        ) as client:
            res = await client.post("/api/chat", json={"model": "m"})
            # The hook reads the body, but httpx caches it: the caller still can.
            assert res.json()["message"]["content"] == "hello there"

    asyncio.run(go())

    last = [e for e in recorder.recent() if e.source == "outbound"][-1]
    assert last.target.endswith("/api/chat")
    assert last.response_body is not None
    assert "hello there" in last.response_body
    assert last.response_bytes is not None


def test_outbound_streaming_response_is_not_buffered() -> None:
    # Streaming responses must pass through untouched: the hook neither captures
    # their body nor consumes the stream the caller is iterating.
    recorder.clear()
    ndjson = b'{"response": "a"}\n{"response": "b"}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/x-ndjson"}, content=ndjson
        )

    async def go() -> list[str]:
        lines: list[str] = []
        async with instrumented_client(
            transport=httpx.MockTransport(handler), base_url="http://localhost:11434"
        ) as client:
            async with client.stream("POST", "/api/generate", json={}) as res:
                async for line in res.aiter_lines():
                    if line:
                        lines.append(line)
        return lines

    lines = asyncio.run(go())
    assert len(lines) == 2, "the caller must still receive the full stream"

    last = [e for e in recorder.recent() if e.source == "outbound"][-1]
    assert last.target.endswith("/api/generate")
    # Read directly (no tee): the body stays uncaptured.
    assert last.response_body is None


def test_streaming_response_is_captured_via_tee() -> None:
    # tee_stream observes a stream without consuming it: the caller still gets
    # every line, and the recorded event is amended with the assembled body.
    recorder.clear()
    ndjson = b'{"response": "hel"}\n{"response": "lo"}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/x-ndjson"}, content=ndjson
        )

    async def go() -> list[str]:
        lines: list[str] = []
        async with instrumented_client(
            transport=httpx.MockTransport(handler), base_url="http://localhost:11434"
        ) as client:
            async with client.stream("POST", "/api/generate", json={}) as res:
                async for line in tee_stream(res, res.aiter_lines()):
                    if line:
                        lines.append(line)
        return lines

    lines = asyncio.run(go())
    assert len(lines) == 2, "tee must not swallow any of the stream"

    last = [e for e in recorder.recent() if e.source == "outbound"][-1]
    assert last.response_body is not None
    assert "hel" in last.response_body and "lo" in last.response_body
    assert last.response_bytes is not None


def test_safe_body_truncates_to_max_chars() -> None:
    big = b"x" * 5000
    assert safe_body(big, sensitive=False, max_chars=10) == "x" * 10 + "… [truncated]"
    # A cap above the body length keeps the whole thing.
    assert safe_body(big, sensitive=False, max_chars=10000) == "x" * 5000


def test_max_body_chars_reads_setting(tmp_path, monkeypatch) -> None:
    import json

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # Unset → the default that mirrors the frontend declaration.
    assert _max_body_chars() == _MAX_BODY_CHARS
    # An override is honored, clamped to the hard capture ceiling.
    (tmp_path / "settings.json").write_text(
        json.dumps({"observability.maxBodyChars": 8000})
    )
    assert _max_body_chars() == 8000
    (tmp_path / "settings.json").write_text(
        json.dumps({"observability.maxBodyChars": 10_000_000})
    )
    assert _max_body_chars() == 65536
    # A garbage value falls back to the default rather than raising.
    (tmp_path / "settings.json").write_text(
        json.dumps({"observability.maxBodyChars": "lots"})
    )
    assert _max_body_chars() == _MAX_BODY_CHARS


def test_outbound_body_truncation_respects_setting(tmp_path, monkeypatch) -> None:
    import json

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(
        json.dumps({"observability.maxBodyChars": 20})
    )
    recorder.clear()
    payload = {"prompt": "p" * 200}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answer": "a" * 200})

    async def go() -> None:
        async with instrumented_client(
            transport=httpx.MockTransport(handler), base_url="http://localhost:11434"
        ) as client:
            await client.post("/api/chat", json=payload)

    asyncio.run(go())

    last = [e for e in recorder.recent() if e.source == "outbound"][-1]
    assert last.request_body is not None
    assert last.response_body is not None
    assert last.request_body.endswith("… [truncated]")
    assert last.response_body.endswith("… [truncated]")
    # 20 captured chars + the truncation marker — far short of the 200-char bodies.
    assert len(last.response_body) < 50
