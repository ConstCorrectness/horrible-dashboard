import pytest
from fastapi.testclient import TestClient

from backend.app import APP_VERSION, app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_health(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["version"] == APP_VERSION


def test_dashboard_layout_default_then_roundtrip(client: TestClient) -> None:
    res = client.get("/api/dashboard/layout")
    assert res.status_code == 200
    assert res.json()["widgets"] == [
        "dashboard.welcome",
        "dashboard.backendStatus",
    ]

    new_layout = {"widgets": ["dashboard.backendStatus"]}
    res = client.put("/api/dashboard/layout", json=new_layout)
    assert res.status_code == 200
    assert res.json() == new_layout

    res = client.get("/api/dashboard/layout")
    assert res.json() == new_layout


def test_dashboard_layout_rejects_bad_payload(client: TestClient) -> None:
    res = client.put("/api/dashboard/layout", json={"widgets": "not-a-list"})
    assert res.status_code == 422


def test_ws_hello(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
    assert msg["channel"] == "system"
    assert msg["event"] == "hello"
