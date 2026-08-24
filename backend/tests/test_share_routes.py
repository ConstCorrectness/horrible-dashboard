"""The `/api/share` HTTP boundary.

These go through `TestClient` rather than calling the manager directly on
purpose: a Pydantic `response_model` silently filters any field it does not
declare, so a served field that the browser never sees looks perfectly healthy
from inside the process. The response body is the thing under test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.share.session import ShareManager


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def fresh_session(monkeypatch):
    """A clean manager per test — the real one is process-global, so without this
    a session started in one test would still be running in the next."""
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.routes.share_manager", mgr)
    monkeypatch.setattr("backend.modules.share.session.share_manager", mgr)
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    class FakeProfile:
        person_id = "hostperson"
        display_name = "Host"

    monkeypatch.setattr(
        "backend.modules.social.roster.self_profile", lambda: FakeProfile()
    )
    return mgr


def test_idle_node_reports_nothing_shared(client: TestClient):
    body = client.get("/api/share").json()
    assert body["hosting"] is None
    assert body["joined"] == []
    assert body["invites"] == []


def test_starting_a_session_serves_the_whole_shape(client: TestClient):
    body = client.post("/api/share/session", json={"title": "demo"}).json()
    # Every field the pane renders has to survive the response model.
    assert set(body) >= {
        "id",
        "title",
        "mode",
        "host_node",
        "host_person",
        "created_at",
        "participants",
        "revision",
        "link",
    }
    assert body["title"] == "demo"
    assert body["mode"] == "semantic"
    # No public link until one is explicitly minted — a session is fabric-only
    # until somebody asks for a URL.
    assert body["link"] == ""


def test_the_host_is_a_participant_with_every_field_the_pane_needs(client: TestClient):
    body = client.post("/api/share/session", json={"title": "demo"}).json()
    (host,) = body["participants"]
    assert set(host) >= {
        "person_id",
        "node_id",
        "name",
        "role",
        "grant",
        "joined_at",
        "following",
    }
    assert host["role"] == "host"


def test_state_reflects_a_running_session(client: TestClient):
    started = client.post("/api/share/session", json={"title": "demo"}).json()
    body = client.get("/api/share").json()
    assert body["hosting"]["id"] == started["id"]


def test_stopping_clears_it(client: TestClient):
    client.post("/api/share/session", json={"title": "demo"})
    assert client.delete("/api/share/session").json() == {
        "ok": True,
        "error": None,
        "detail": None,
    }
    assert client.get("/api/share").json()["hosting"] is None


def test_inviting_without_a_session_fails_loudly(client: TestClient):
    body = client.post("/api/share/invite", json={"person_id": "someone"}).json()
    assert body["ok"] is False
    assert body["error"]


def test_granting_to_a_non_participant_is_reported_not_silently_accepted(
    client: TestClient,
):
    client.post("/api/share/session", json={"title": "demo"})
    body = client.post(
        "/api/share/grant", json={"person_id": "nobody", "grant": "edit"}
    ).json()
    assert body["ok"] is False


def test_a_grant_outside_the_ladder_is_rejected_at_the_boundary(client: TestClient):
    """The rung names are a closed vocabulary. A typo must 422 here rather than
    reaching the gate, where an unknown value reads as `view` and would look like
    the grant silently did nothing."""
    client.post("/api/share/session", json={"title": "demo"})
    resp = client.post(
        "/api/share/grant", json={"person_id": "gp", "grant": "superuser"}
    )
    assert resp.status_code == 422


def test_revoke_all_is_safe_with_no_session(client: TestClient):
    assert client.post("/api/share/revoke-all").json()["ok"] is True
