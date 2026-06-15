import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_empty_collection(client: TestClient) -> None:
    res = client.get("/api/workspaces")
    assert res.status_code == 200
    assert res.json() == {"active": None, "workspaces": []}


def test_create_sets_first_active(client: TestClient) -> None:
    res = client.post("/api/workspaces", json={"name": "Scratchpad"})
    assert res.status_code == 200
    ws = res.json()
    assert ws["name"] == "Scratchpad"
    assert ws["layout"] is None

    state = client.get("/api/workspaces").json()
    assert state["active"] == ws["id"]
    assert [w["id"] for w in state["workspaces"]] == [ws["id"]]


def test_upsert_by_id_creates_and_roundtrips_opaque_layout(client: TestClient) -> None:
    # The frontend seeds the Dashboard with a known id via PUT (upsert).
    layout = {"grid": {"root": {"type": "branch", "data": [1, 2]}}, "panels": {"a": {}}}
    res = client.put(
        "/api/workspaces/dashboard", json={"name": "Dashboard", "layout": layout}
    )
    assert res.status_code == 200
    assert res.json() == {"id": "dashboard", "name": "Dashboard", "layout": layout}

    state = client.get("/api/workspaces").json()
    assert state["active"] == "dashboard"
    assert state["workspaces"][0]["layout"] == layout


def test_rename_does_not_clobber_layout(client: TestClient) -> None:
    layout = {"grid": {"x": 1}}
    client.put(
        "/api/workspaces/dashboard", json={"name": "Dashboard", "layout": layout}
    )
    # A name-only PUT must leave the layout intact (partial update).
    res = client.put("/api/workspaces/dashboard", json={"name": "Home"})
    assert res.status_code == 200
    assert res.json() == {"id": "dashboard", "name": "Home", "layout": layout}


def test_set_active_unknown_is_404(client: TestClient) -> None:
    assert client.put("/api/workspaces/active", json={"id": "nope"}).status_code == 404


def test_set_active_switches(client: TestClient) -> None:
    a = client.post("/api/workspaces", json={"name": "A"}).json()
    b = client.post("/api/workspaces", json={"name": "B"}).json()
    assert client.get("/api/workspaces").json()["active"] == a["id"]

    res = client.put("/api/workspaces/active", json={"id": b["id"]})
    assert res.status_code == 200
    assert res.json()["active"] == b["id"]


def test_delete_active_reassigns(client: TestClient) -> None:
    a = client.post("/api/workspaces", json={"name": "A"}).json()
    b = client.post("/api/workspaces", json={"name": "B"}).json()
    client.put("/api/workspaces/active", json={"id": b["id"]})

    state = client.delete(f"/api/workspaces/{b['id']}").json()
    assert [w["id"] for w in state["workspaces"]] == [a["id"]]
    assert state["active"] == a["id"]  # fell back to the remaining one

    # Deleting the last one clears active.
    state = client.delete(f"/api/workspaces/{a['id']}").json()
    assert state == {"active": None, "workspaces": []}


def test_bad_id_rejected(client: TestClient) -> None:
    assert client.put("/api/workspaces/..bad", json={"layout": {}}).status_code == 422
