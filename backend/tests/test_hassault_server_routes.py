"""The Server pane's routes: a room's roster, and fielding bots in it.

They wrap the agent tools rather than re-deriving the room, so the pane and the
agent describe one room one way — what is pinned here is that the wrapper keeps
the tool's answer and turns its `error` into a status code rather than a 200.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.hassault.match import match_server

API = "/api/hassault"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def room():
    made = match_server.create("hd_pit")
    yield made
    match_server.rooms.pop(made.id, None)


def test_roster_lists_bots_added_through_the_route(client, room):
    added = client.post(
        f"{API}/matches/{room.id}/bots", json={"count": 2, "skill": "easy"}
    )
    assert added.status_code == 200, added.text
    assert len(added.json()["added"]) == 2

    roster = client.get(f"{API}/matches/{room.id}").json()
    assert roster["room"] == room.id
    assert roster["map"] == "hd_pit"
    assert [p["bot"] for p in roster["players"]] == [True, True]


def test_removing_bots_empties_the_roster(client, room):
    client.post(f"{API}/matches/{room.id}/bots", json={"count": 3})
    removed = client.delete(f"{API}/matches/{room.id}/bots")
    assert removed.status_code == 200
    assert removed.json()["removed"] == 3
    assert client.get(f"{API}/matches/{room.id}").json()["players"] == []


def test_an_unknown_room_is_a_404_not_an_empty_roster(client):
    assert client.get(f"{API}/matches/nope").status_code == 404


def test_an_unknown_skill_is_refused(client, room):
    res = client.post(f"{API}/matches/{room.id}/bots", json={"skill": "godlike"})
    assert res.status_code == 422
