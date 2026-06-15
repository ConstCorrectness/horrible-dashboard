import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_settings_default_empty(client: TestClient) -> None:
    res = client.get("/api/settings")
    assert res.status_code == 200
    assert res.json() == {"values": {}}


def test_put_then_get_roundtrip(client: TestClient) -> None:
    res = client.put("/api/settings/observability.recentCount", json={"value": 2})
    assert res.status_code == 200
    assert res.json() == {"value": 2}

    # A second key of a different type coexists in the bag.
    client.put("/api/settings/hello-widget.greeting", json={"value": "hi"})

    values = client.get("/api/settings").json()["values"]
    assert values == {"observability.recentCount": 2, "hello-widget.greeting": "hi"}


def test_delete_resets_key(client: TestClient) -> None:
    client.put("/api/settings/observability.recentCount", json={"value": 9})
    res = client.delete("/api/settings/observability.recentCount")
    assert res.status_code == 200
    assert res.json() == {"values": {}}
    assert client.get("/api/settings").json() == {"values": {}}


def test_delete_missing_key_is_noop(client: TestClient) -> None:
    res = client.delete("/api/settings/never.set")
    assert res.status_code == 200
    assert res.json() == {"values": {}}


def test_put_rejects_bad_key(client: TestClient) -> None:
    # Slashes/leading dots aren't valid setting keys.
    res = client.put("/api/settings/..bad", json={"value": 1})
    assert res.status_code == 422


def test_put_rejects_missing_value(client: TestClient) -> None:
    res = client.put("/api/settings/observability.recentCount", json={})
    assert res.status_code == 422
