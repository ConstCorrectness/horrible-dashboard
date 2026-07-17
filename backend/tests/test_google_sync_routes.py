"""The Drive sync trigger route — the replacement for the deleted
`POST /api/integrations/google/sync`."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.connectors import store
from backend.modules.connectors.store import Credential


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def connected():
    store.save("google", Credential(access_token="at", refresh_token="rt"))
    yield
    store.clear("google")


def test_sync_requires_a_connection(client: TestClient):
    """409 rather than silently queueing work that would no-op — the old handler just
    logged "no credentials found" and returned, which looks identical to success."""
    res = client.post("/api/connectors/google/sync")
    assert res.status_code == 409
    assert "isn't connected" in res.json()["detail"]


def test_sync_queues_a_task(client: TestClient, connected):
    res = client.post("/api/connectors/google/sync")
    assert res.status_code == 200
    body = res.json()
    assert body["task_id"]
    assert body["library"] == "google_drive"
    assert body["full"] is False


def test_sync_honours_an_explicit_library_and_full(client: TestClient, connected):
    res = client.post(
        "/api/connectors/google/sync", json={"library": "work-docs", "full": True}
    )
    body = res.json()
    assert body["library"] == "work-docs"
    assert body["full"] is True


def test_sync_uses_the_configured_library(client: TestClient, connected):
    from backend.modules.settings.routes import set_value

    set_value("connectors.google.driveLibrary", "notes")
    assert client.post("/api/connectors/google/sync").json()["library"] == "notes"


def test_sync_status_before_any_run(client: TestClient):
    body = client.get("/api/connectors/google/sync").json()
    assert body["library"] == "google_drive"
    assert body["synced"] is False
    assert body["files"] == 0


def test_sync_status_reflects_state(client: TestClient):
    from backend.modules.connectors.providers import google_sync

    google_sync.set_start_page_token("google_drive", "tok1")
    body = client.get("/api/connectors/google/sync").json()
    assert body["synced"] is True


def test_sync_status_can_target_a_named_library(client: TestClient):
    body = client.get("/api/connectors/google/sync", params={"library": "other"}).json()
    assert body["library"] == "other"
