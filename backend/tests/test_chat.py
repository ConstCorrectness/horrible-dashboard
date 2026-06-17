import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_empty_collection(client: TestClient) -> None:
    res = client.get("/api/chat/sessions")
    assert res.status_code == 200
    assert res.json() == {"active": None, "sessions": []}


def test_create_sets_first_active(client: TestClient) -> None:
    res = client.post("/api/chat/sessions", json={"title": "Layout help"})
    assert res.status_code == 200
    session = res.json()
    assert session["title"] == "Layout help"
    assert session["messages"] == []

    state = client.get("/api/chat/sessions").json()
    assert state["active"] == session["id"]
    # The list view is metadata only — no messages field.
    assert state["sessions"][0] == {
        "id": session["id"],
        "title": "Layout help",
        "updated": session["updated"],
    }


def test_get_session_returns_full_transcript(client: TestClient) -> None:
    session = client.post("/api/chat/sessions", json={}).json()
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "hello",
            "reasoning": "be nice",
            "actions": [],
        },
    ]
    client.put(f"/api/chat/sessions/{session['id']}", json={"messages": messages})

    res = client.get(f"/api/chat/sessions/{session['id']}")
    assert res.status_code == 200
    got = res.json()
    assert [m["content"] for m in got["messages"]] == ["hi", "hello"]
    assert got["messages"][1]["reasoning"] == "be nice"


def test_get_unknown_is_404(client: TestClient) -> None:
    assert client.get("/api/chat/sessions/nope").status_code == 404


def test_rename_does_not_clobber_messages(client: TestClient) -> None:
    session = client.post("/api/chat/sessions", json={}).json()
    messages = [{"role": "user", "content": "keep me"}]
    client.put(f"/api/chat/sessions/{session['id']}", json={"messages": messages})

    # A title-only PUT must leave the messages intact (partial update).
    res = client.put(f"/api/chat/sessions/{session['id']}", json={"title": "Renamed"})
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Renamed"
    assert [m["content"] for m in body["messages"]] == ["keep me"]


def test_set_active_unknown_is_404(client: TestClient) -> None:
    assert (
        client.put("/api/chat/sessions/active", json={"id": "nope"}).status_code == 404
    )


def test_delete_active_reassigns(client: TestClient) -> None:
    a = client.post("/api/chat/sessions", json={"title": "A"}).json()
    b = client.post("/api/chat/sessions", json={"title": "B"}).json()
    client.put("/api/chat/sessions/active", json={"id": b["id"]})

    state = client.delete(f"/api/chat/sessions/{b['id']}").json()
    assert [s["id"] for s in state["sessions"]] == [a["id"]]
    assert state["active"] == a["id"]  # fell back to the remaining one

    state = client.delete(f"/api/chat/sessions/{a['id']}").json()
    assert state == {"active": None, "sessions": []}


def test_bad_id_rejected(client: TestClient) -> None:
    assert (
        client.put("/api/chat/sessions/..bad", json={"title": "x"}).status_code == 422
    )
