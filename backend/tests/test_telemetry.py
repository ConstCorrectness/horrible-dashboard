import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.telemetry.instrument import REDACTED, instrumented_client
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
