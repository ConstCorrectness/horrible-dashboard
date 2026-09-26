"""The game server as the whole backend of the standalone web client.

`apps/assault-web` has no node behind it: every request it makes lands on the
game server. In dev that was hidden — Vite proxied `/api` to a node and only
`/hassault-ws` here — so a deployed build 404ed on its first map fetch. These
tests pin what the web client needs from this server, and the limits that make
it safe to leave open to the internet.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.games_server import hassault_rooms
from backend.games_server.hassault_rooms import HassaultReferee, SeatConn


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def guest(name: str) -> SeatConn:
    return SeatConn(FakeSocket(), f"guest_{name}", name, is_guest=True)


@pytest.fixture
def server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server.app import app

    return TestClient(app)


@pytest.fixture
def referee(tmp_path, monkeypatch) -> HassaultReferee:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return HassaultReferee()


def _join(ws, **data) -> dict:
    ws.send_text(json.dumps({"channel": "hassault", "event": "join", "data": data}))
    for _ in range(50):
        msg = json.loads(ws.receive_text())
        if msg["event"] in ("welcome", "error"):
            return msg
    raise AssertionError("no answer to a join")


# ---------------------------------------------------------------------------
# Content: the node's own handlers, bundled maps only
# ---------------------------------------------------------------------------


def test_the_boot_path_is_served(server):
    """Every GET the pane makes before its first frame."""
    for path in (
        "/api/hassault/status",
        "/api/hassault/maps",
        "/api/hassault/maps/hd_pit",
        "/api/hassault/maps/hd_pit/cubes",
        "/api/hassault/weapons",
        "/api/hassault/items",
        "/api/hassault/tacticals",
        "/api/hassault/throw",
        "/api/hassault/modes",
        "/api/hassault/hitbox",
    ):
        assert server.get(path).status_code == 200, path


def test_the_numbers_are_the_nodes_own(server):
    """Served by the same handler, so a client predicting against a node and one
    predicting against this server are predicting against the same rifle."""
    from backend.modules.hassault import routes as node

    weapons = server.get("/api/hassault/weapons").json()
    expected = [w.model_dump(mode="json") for w in asyncio.run(node.get_weapons())]
    assert weapons == expected

    cubes = server.get("/api/hassault/maps/hd_pit/cubes").content
    assert cubes == asyncio.run(node.get_map_cubes("hd_pit")).body


def test_only_bundled_maps_are_served(server):
    names = {m["name"] for m in server.get("/api/hassault/maps").json()}
    from backend.modules.hassault import mapsource

    assert names == set(mapsource.bundled_names())
    # A draft resolves through `assets.load_map` on a node; it must not here.
    for name in ("draft:abc", "ac_desert", "..%2F..%2Fsecrets"):
        assert server.get(f"/api/hassault/maps/{name}").status_code == 404
        assert server.get(f"/api/hassault/maps/{name}/cubes").status_code == 404


def test_nothing_that_writes_is_exposed(server):
    """Hitbox tuning is live and process-global on a node: here it would let any
    visitor resize everybody's body."""
    assert server.put("/api/hassault/hitbox", json={"radius": 9}).status_code == 405
    assert server.post("/api/hassault/maps/drafts", json={}).status_code in (404, 405)
    assert server.post("/api/hassault/console/exec", json={}).status_code == 404
    assert server.get("/api/hassault/skins/inventory").status_code == 404


def test_cors_is_closed_unless_configured(server, monkeypatch):
    res = server.get(
        "/api/hassault/maps", headers={"Origin": "https://somebody-else.example"}
    )
    assert "access-control-allow-origin" not in res.headers

    from backend.games_server.app import _web_origins

    monkeypatch.setenv(
        "GAMES_WEB_ORIGINS", " https://play.example.com/ , https://b.example ,"
    )
    assert _web_origins() == ["https://play.example.com", "https://b.example"]


# ---------------------------------------------------------------------------
# The socket
# ---------------------------------------------------------------------------


def test_the_welcome_is_the_rooms_whole_state(server):
    """Items, what is taken, and the mode arrive with the welcome — as a node
    sends them. With five fields the client drew no pickups at all."""
    with server.websocket_connect("/hassault-ws?guest=1&name=Rookie") as ws:
        welcome = _join(ws, map="hd_pit")["data"]
    assert welcome["items"], "hd_pit carries an item layout"
    assert welcome["itemsOut"] == []
    assert welcome["mode"]["id"] == "dm"
    assert welcome["playerId"] and welcome["rated"] is False


def test_ping_is_answered_by_the_simulating_server(server):
    with server.websocket_connect("/hassault-ws?guest=1&name=Rookie") as ws:
        _join(ws, map="hd_pit")
        for t in (1234.5, {"not": "a number"}):
            ws.send_text(
                json.dumps({"channel": "hassault", "event": "ping", "data": {"t": t}})
            )
            for _ in range(200):
                msg = json.loads(ws.receive_text())
                if msg["event"] == "pong":
                    break
            else:
                pytest.fail("no pong")
            assert msg["data"]["t"] == (t if isinstance(t, float) else None)
            assert isinstance(msg["data"]["serverT"], int)


def test_a_share_link_to_a_closed_room_lands_on_its_map(server):
    with server.websocket_connect("/hassault-ws?guest=1&name=Late") as ws:
        msg = _join(ws, map="hd_pit", room="gone1234")
    assert msg["event"] == "welcome"
    assert msg["data"]["map"] == "hd_pit"
    assert msg["data"]["room"] != "gone1234"


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_the_server_caps_how_many_rooms_strangers_can_open(referee, monkeypatch):
    monkeypatch.setenv("HASSAULT_MAX_ROOMS", "1")

    async def go():
        try:
            first = await referee.join(guest("a"), "hd_pit")
            with pytest.raises(ValueError, match="full"):
                await referee.join(guest("b"), "hd_atrium")
            # Joining the room that exists is never what the cap refuses.
            second = await referee.join(guest("c"), "hd_pit")
            assert second["room"] == first["room"]
        finally:
            await referee.shutdown()

    asyncio.run(go())


def test_server_rooms_record_no_replays(referee):
    """A replay kept every tick in memory for the room's whole life — a public
    room that never empties was an out-of-memory crash on a timer."""

    async def go():
        try:
            welcome = await referee.join(guest("a"), "hd_pit")
            assert referee.server.get(welcome["room"]).recorder is None
        finally:
            await referee.shutdown()

    asyncio.run(go())


def test_max_rooms_reads_a_bad_value_as_the_default(monkeypatch):
    monkeypatch.setenv("HASSAULT_MAX_ROOMS", "lots")
    assert hassault_rooms.max_rooms() == 12
    monkeypatch.setenv("HASSAULT_MAX_ROOMS", "0")
    assert hassault_rooms.max_rooms() == 1
