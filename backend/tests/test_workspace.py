import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_layout_absent_returns_null(client: TestClient) -> None:
    res = client.get("/api/workspace/layout")
    assert res.status_code == 200
    assert res.json() == {"layout": None}


def test_layout_roundtrips_opaque_json(client: TestClient) -> None:
    # An arbitrary nested shape — the backend must round-trip it untouched.
    layout = {"grid": {"root": {"type": "branch", "data": [1, 2]}}, "panels": {"a": {}}}
    res = client.put("/api/workspace/layout", json={"layout": layout})
    assert res.status_code == 200
    assert res.json()["layout"] == layout

    res = client.get("/api/workspace/layout")
    assert res.json()["layout"] == layout
