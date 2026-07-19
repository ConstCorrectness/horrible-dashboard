"""Per-agent chat sessions: the additive `agent_id` field, the `?agent=` filter,
and backwards compatibility with pre-roster `chat-sessions.json` files."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_sessions_filter_by_agent(client: TestClient) -> None:
    main = client.post("/api/chat/sessions", json={"title": "M"}).json()
    coder = client.post(
        "/api/chat/sessions", json={"title": "C", "agent_id": "coder"}
    ).json()
    assert coder["agent_id"] == "coder"

    everything = client.get("/api/chat/sessions").json()
    assert {s["id"] for s in everything["sessions"]} == {main["id"], coder["id"]}

    only_coder = client.get("/api/chat/sessions", params={"agent": "coder"}).json()
    assert [s["id"] for s in only_coder["sessions"]] == [coder["id"]]
    assert only_coder["active"] == coder["id"]

    only_main = client.get("/api/chat/sessions", params={"agent": "main"}).json()
    assert [s["id"] for s in only_main["sessions"]] == [main["id"]]
    assert only_main["active"] == main["id"]


def test_active_is_tracked_per_agent(client: TestClient) -> None:
    m1 = client.post("/api/chat/sessions", json={"title": "M1"}).json()
    c1 = client.post(
        "/api/chat/sessions", json={"title": "C1", "agent_id": "dba"}
    ).json()
    m2 = client.post("/api/chat/sessions", json={"title": "M2"}).json()

    # Creating m2 must not steal dba's active pointer (or vice versa).
    assert (
        client.get("/api/chat/sessions", params={"agent": "dba"}).json()["active"]
        == c1["id"]
    )
    assert (
        client.get("/api/chat/sessions", params={"agent": "main"}).json()["active"]
        == m2["id"]
    )

    client.put("/api/chat/sessions/active", json={"id": m1["id"]})
    assert (
        client.get("/api/chat/sessions", params={"agent": "main"}).json()["active"]
        == m1["id"]
    )
    assert (
        client.get("/api/chat/sessions", params={"agent": "dba"}).json()["active"]
        == c1["id"]
    )


def test_delete_reassigns_within_the_agent(client: TestClient) -> None:
    a = client.post(
        "/api/chat/sessions", json={"title": "A", "agent_id": "coder"}
    ).json()
    b = client.post(
        "/api/chat/sessions", json={"title": "B", "agent_id": "coder"}
    ).json()

    client.delete(f"/api/chat/sessions/{b['id']}")
    assert (
        client.get("/api/chat/sessions", params={"agent": "coder"}).json()["active"]
        == a["id"]
    )

    client.delete(f"/api/chat/sessions/{a['id']}")
    state = client.get("/api/chat/sessions", params={"agent": "coder"}).json()
    assert state == {"active": None, "sessions": []}


def test_upsert_creates_with_agent_id(client: TestClient) -> None:
    res = client.put(
        "/api/chat/sessions/abc123",
        json={"title": "T", "agent_id": "researcher", "messages": []},
    )
    assert res.status_code == 200
    assert res.json()["agent_id"] == "researcher"
    only = client.get("/api/chat/sessions", params={"agent": "researcher"}).json()
    assert [s["id"] for s in only["sessions"]] == ["abc123"]


def test_legacy_pre_roster_file_still_loads(client: TestClient, tmp_path) -> None:
    # A file written before the roster existed: no agent_id fields, no
    # active_by_agent map. It must validate (a schema break would silently wipe
    # the user's history) and read as main's sessions.
    legacy = {
        "active": "old1",
        "sessions": [
            {
                "id": "old1",
                "title": "Old chat",
                "messages": [{"role": "user", "content": "hi"}],
                "created": 1.0,
                "updated": 2.0,
            }
        ],
    }
    (tmp_path / "chat-sessions.json").write_text(json.dumps(legacy))

    state = client.get("/api/chat/sessions", params={"agent": "main"}).json()
    assert state["active"] == "old1"
    assert [s["id"] for s in state["sessions"]] == ["old1"]
    assert state["sessions"][0]["agent_id"] == "main"
