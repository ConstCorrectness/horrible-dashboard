"""Tests for the notes module (C2): CRUD, search, and revision-conflict saves."""

import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def _create(client: TestClient, title="Untitled", content="") -> dict:
    res = client.post("/api/notes", json={"title": title, "content": content})
    assert res.status_code == 200
    return res.json()


def test_create_starts_at_revision_1(client: TestClient) -> None:
    note = _create(client, "First", "body")
    assert note["revision"] == 1
    assert note["title"] == "First"
    assert note["content"] == "body"


def test_list_is_newest_first(client: TestClient) -> None:
    a = _create(client, "A")
    b = _create(client, "B")
    ids = [n["id"] for n in client.get("/api/notes").json()]
    assert ids[:2] == [b["id"], a["id"]]
    # list items are lightweight (no content field beyond meta)
    assert "content" not in client.get("/api/notes").json()[0]


def test_get_missing_is_404(client: TestClient) -> None:
    assert client.get("/api/notes/abc123").status_code == 404


def test_update_bumps_revision(client: TestClient) -> None:
    note = _create(client, "T", "v1")
    res = client.put(
        f"/api/notes/{note['id']}",
        json={"content": "v2", "base_revision": 1},
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["content"] == "v2"
    assert updated["revision"] == 2


def test_partial_update_keeps_other_fields(client: TestClient) -> None:
    note = _create(client, "Keep", "body")
    res = client.put(
        f"/api/notes/{note['id']}",
        json={"title": "Renamed", "base_revision": 1},
    )
    assert res.status_code == 200
    assert res.json()["title"] == "Renamed"
    assert res.json()["content"] == "body"


def test_stale_save_conflicts_and_returns_current(client: TestClient) -> None:
    note = _create(client, "T", "v1")
    # First save succeeds (1 → 2).
    client.put(f"/api/notes/{note['id']}", json={"content": "v2", "base_revision": 1})
    # A second save still using base_revision 1 is stale → 409 with current note.
    res = client.put(
        f"/api/notes/{note['id']}", json={"content": "v3", "base_revision": 1}
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["message"] == "revision conflict"
    assert detail["current"]["revision"] == 2
    assert detail["current"]["content"] == "v2"


def test_search_matches_title_and_content(client: TestClient) -> None:
    _create(client, "Groceries", "milk and eggs")
    _create(client, "Ideas", "build a dashboard")
    by_title = client.get("/api/notes/search", params={"q": "groc"}).json()
    assert [n["title"] for n in by_title] == ["Groceries"]
    by_content = client.get("/api/notes/search", params={"q": "dashboard"}).json()
    assert [n["title"] for n in by_content] == ["Ideas"]
    assert by_content[0]["snippet"] is not None


def test_search_empty_query_returns_nothing(client: TestClient) -> None:
    _create(client, "X", "y")
    assert client.get("/api/notes/search", params={"q": "  "}).json() == []


def test_delete(client: TestClient) -> None:
    note = _create(client, "Doomed")
    res = client.delete(f"/api/notes/{note['id']}")
    assert res.status_code == 200
    assert client.get(f"/api/notes/{note['id']}").status_code == 404


def test_notes_persist_across_clients(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    note = _create(client, "Durable", "stays")
    # A fresh client over the same data dir sees it.
    fresh = TestClient(app)
    res = fresh.get(f"/api/notes/{note['id']}")
    assert res.status_code == 200
    assert res.json()["content"] == "stays"
