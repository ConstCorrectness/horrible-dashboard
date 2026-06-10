import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.telemetry.instrument import instrumented_client
from backend.modules.telemetry.recorder import recorder

ALLOWED_FIELDS = {
    "id",
    "ts",
    "source",
    "method",
    "target",
    "status",
    "duration_ms",
    "request_bytes",
    "response_bytes",
    "error",
}


@pytest.fixture
def client() -> TestClient:
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


def test_events_are_metadata_only(client: TestClient) -> None:
    # A POST with a body must not leak the body — only metadata fields exist.
    client.put("/api/dashboard/layout", json={"widgets": ["dashboard.welcome"]})
    events = client.get("/api/telemetry/recent").json()
    put = [e for e in events if e["target"] == "/api/dashboard/layout"][-1]
    assert set(put.keys()) <= ALLOWED_FIELDS
    assert "widgets" not in str(put)


def test_outbound_call_is_recorded() -> None:
    recorder.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async def go() -> None:
        async with instrumented_client(
            transport=httpx.MockTransport(handler), base_url="https://clubhouse.test"
        ) as client:
            await client.post(
                "/start_phone_number_auth", json={"phone_number": "+15551234567"}
            )

    asyncio.run(go())

    outbound = [e for e in recorder.recent() if e.source == "outbound"]
    assert outbound, "outbound call should be recorded"
    assert outbound[-1].method == "POST"
    assert outbound[-1].target == "https://clubhouse.test/start_phone_number_auth"
    assert outbound[-1].status == 200
    assert outbound[-1].duration_ms is not None
