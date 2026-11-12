"""Map drafts on a game server: off by default, whole when switched on.

The native client's map editor used to need a dashboard node, because that is
where drafts lived. A game server somebody runs for themselves can keep them
too (`HASSAULT_ENABLE_DRAFTS=1`), which is what lets the editor run with no
dashboard at all. The public server leaves it off: its content routes have no
accounts, and a draft can be saved as a file.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HASSAULT_ENABLE_DRAFTS", raising=False)
    from backend.games_server.app import app

    return TestClient(app)


def test_a_public_server_keeps_no_drafts(server):
    res = server.post("/api/hassault/maps/drafts", json={})
    assert res.status_code == 404
    assert "no map drafts" in res.json()["detail"]
    assert server.get("/api/hassault/maps/drafts/abc").status_code == 404
    # And a draft is not a map it will serve.
    assert server.get("/api/hassault/maps/draft:abc").status_code == 404


def test_a_server_that_keeps_drafts_serves_the_editor_its_whole_boot(
    server, monkeypatch
):
    monkeypatch.setenv("HASSAULT_ENABLE_DRAFTS", "1")
    opened = server.post("/api/hassault/maps/drafts", json={"from": "hd_pit"})
    assert opened.status_code == 200, opened.text
    draft = opened.json()
    assert draft["mapName"] == f"draft:{draft['id']}"

    # The editor loads a draft as a map: info, then cubes, then who painted what.
    info = server.get(f"/api/hassault/maps/{draft['mapName']}")
    assert info.status_code == 200, info.text
    cubes = server.get(f"/api/hassault/maps/{draft['mapName']}/cubes")
    assert cubes.status_code == 200
    assert len(cubes.content) > 0
    owners = server.get(f"/api/hassault/maps/drafts/{draft['id']}/owners")
    assert owners.status_code == 200

    assert server.delete(f"/api/hassault/maps/drafts/{draft['id']}").json() == {
        "closed": True
    }


def test_the_switch_is_read_per_request(server, monkeypatch):
    monkeypatch.setenv("HASSAULT_ENABLE_DRAFTS", "1")
    draft = server.post("/api/hassault/maps/drafts", json={}).json()
    monkeypatch.setenv("HASSAULT_ENABLE_DRAFTS", "0")
    assert server.get(f"/api/hassault/maps/drafts/{draft['id']}").status_code == 404
    monkeypatch.setenv("HASSAULT_ENABLE_DRAFTS", "1")
    assert server.delete(f"/api/hassault/maps/drafts/{draft['id']}").status_code == 200
