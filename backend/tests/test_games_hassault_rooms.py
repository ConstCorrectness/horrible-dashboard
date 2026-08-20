"""Server-hosted HorribleAssault: the referee, and why it is the trust boundary.

A match used to be simulated only inside a player's own node, which meant that
when you hosted one, your machine decided how many kills you had. Moving the row
to a central database would not have fixed that — it would have filed a
self-reported number somewhere more official-looking. **Storage is not the trust
boundary; simulation is.**

So these tests are about the boundary: that the server runs the same simulation
the clients do, that it will only adjudicate maps everybody has, that it validates
input through the one shared validator, and that what it writes down is its own
account of the match rather than anything a client said.

Async tests run through one `asyncio.run(go())` each, like the rest of the suite:
a single event loop per test, because the match server's tick task lives on it.
"""

from __future__ import annotations

import asyncio
import json
import math

import pytest

from backend.games_server import hassault_rooms, store
from backend.games_server.hassault_rooms import HassaultReferee, SeatConn
from backend.modules.hassault import weapons
from backend.modules.hassault.match import Command


class FakeSocket:
    """Collects what the referee sends, so a test can read the wire."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def seat(name: str = "alice", account: str | None = None) -> SeatConn:
    return SeatConn(FakeSocket(), account or f"acct-{name}", name)


@pytest.fixture
def referee(tmp_path, monkeypatch) -> HassaultReferee:
    """A referee with its own data dir, so the server's store is not the real one."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return HassaultReferee()


@pytest.fixture
def recorded(monkeypatch) -> list[dict]:
    """Capture what the referee hands `store.record_result`."""
    calls: list[dict] = []

    def fake_record(game_id, table_id, seats, returns, winner, **kw):
        calls.append(
            {
                "game_id": game_id,
                "table_id": table_id,
                "seats": seats,
                "returns": returns,
                "winner": winner,
                "rated": kw.get("rated"),
                "ruleset": kw.get("ruleset"),
            }
        )
        return []

    monkeypatch.setattr(store, "record_result", fake_record)
    return calls


# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------


def test_only_bundled_maps_can_be_adjudicated(referee: HassaultReferee):
    """A map that exists on one player's disk cannot be judged by anybody else —
    and this server has no AssaultCube install to read one from anyway. The
    refusal is explicit so the reason is legible."""
    assert referee.playable("hd_pit")
    assert not referee.playable("ac_desert")
    assert set(referee.maps()) == {"hd_atrium", "hd_crossing", "hd_pit"}


def test_joining_an_unbundled_map_is_refused(referee: HassaultReferee):
    async def go():
        with pytest.raises(ValueError):
            await referee.join(seat(), "ac_desert")

    asyncio.run(go())


# ---------------------------------------------------------------------------
# The match is really simulated here
# ---------------------------------------------------------------------------


def test_a_join_opens_a_room_and_seats_the_player(referee: HassaultReferee):
    async def go():
        conn = seat("alice")
        welcome = await referee.join(conn, "hd_pit")
        assert welcome["map"] == "hd_pit"
        assert welcome["rated"] is True
        assert welcome["playerId"]
        # And the player is in the server's own room, not a description of one.
        entry = referee.server.player_for(conn)
        assert entry is not None
        room, player = entry
        assert player.name == "alice"
        assert room.map_name == "hd_pit"

    asyncio.run(go())


def test_two_players_share_a_room_on_the_same_map(referee: HassaultReferee):
    async def go():
        a, b = seat("alice"), seat("bob")
        first = await referee.join(a, "hd_pit")
        second = await referee.join(b, "hd_pit")
        assert first["room"] == second["room"], "the second player opened their own"

    asyncio.run(go())


def test_the_display_name_never_decides_the_account(referee: HassaultReferee):
    """The match knows a nameplate; the ladder needs an account. Keeping the
    mapping on the connection is what stops a player naming themselves into
    somebody else's row."""

    async def go():
        conn = SeatConn(FakeSocket(), "acct-real", "someone-else")
        await referee.join(conn, "hd_pit")
        assert conn.account_id == "acct-real"

    asyncio.run(go())


def test_input_goes_through_the_shared_validator(referee: HassaultReferee):
    """The same `parse_command` a browser's input goes through on a node. A
    second, laxer one on the path that happens to be rated is exactly where a gap
    appears — in the one place nobody looks."""

    async def go():
        conn = seat()
        await referee.join(conn, "hd_pit")
        _, player = referee.server.player_for(conn)

        referee.apply_input(
            conn,
            {
                "commands": [
                    # An analogue axis of 50 is the obvious way to ask to move
                    # fifty times as fast; the validator clamps it to 1.
                    {"seq": 1, "forward": 50, "dt": 0.016},
                    # No sequence number: dropped entirely.
                    {"forward": 1, "dt": 0.016},
                ]
            },
        )
        queued = list(player.queue)
        assert len(queued) == 1, "an unnumbered command was accepted"
        assert queued[0].forward == 1.0

    asyncio.run(go())


def test_a_shot_is_resolved_by_the_server(referee: HassaultReferee):
    """The point of the whole module: damage happens *here*, from geometry this
    process owns, not from a number a client reported."""

    async def go():
        a, b = seat("alice"), seat("bob")
        await referee.join(a, "hd_pit")
        await referee.join(b, "hd_pit")
        room, shooter = referee.server.player_for(a)
        _, victim = referee.server.player_for(b)

        victim.protected_until = 0.0
        shooter.state.x, shooter.state.y = victim.state.x - 12.0, victim.state.y
        shooter.state.z = victim.state.z
        shooter.state.on_ground = True
        shooter.weapon = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["assault"])
        shooter.ammo[shooter.weapon] = 30
        shooter.budget = 1.0
        yaw = math.atan2(
            victim.state.y - shooter.state.y, victim.state.x - shooter.state.x
        )
        room.enqueue(
            shooter,
            Command(
                seq=1,
                forward=0.0,
                strafe=0.0,
                jump=False,
                yaw=yaw,
                pitch=0.0,
                dt=1 / 60,
                fire=True,
            ),
        )
        room.simulate(1 / 60)

        assert victim.health < 100.0
        assert shooter.damage_dealt > 0

    asyncio.run(go())


# ---------------------------------------------------------------------------
# What it writes down
# ---------------------------------------------------------------------------


def test_leaving_records_the_session_as_the_servers_own_word(
    referee: HassaultReferee, recorded: list[dict]
):
    async def go():
        conn = seat("alice")
        await referee.join(conn, "hd_pit")
        _, player = referee.server.player_for(conn)
        player.kills = 4
        player.deaths = 1
        player.head_kills = 2
        player.damage_dealt = 700.0

        result = await referee.leave(conn)
        assert result is not None
        # The flag the node reads to decide whether this may ever count.
        assert result["authority"] == "server"
        assert result["kills"] == 4

        assert len(recorded) == 1
        entry = recorded[0]
        assert entry["game_id"] == hassault_rooms.GAME_ID
        assert entry["seats"] == ["acct-alice"]
        # **Unrated on purpose**: `record_result` applies ELO to two seats facing
        # each other, and a free-for-all people come and go from is not that
        # shape. Inventing a rating for it here is the very thing this module
        # exists to stop.
        assert entry["rated"] is False
        assert entry["ruleset"]["kills"] == 4
        assert entry["ruleset"]["damageDealt"] == 700

    asyncio.run(go())


def test_leaving_twice_records_once(referee: HassaultReferee, recorded: list[dict]):
    """A polite `leave` followed by the socket closing is the normal shape, and
    the disconnect path calls this again. The second call must find nothing."""

    async def go():
        conn = seat()
        await referee.join(conn, "hd_pit")
        assert await referee.leave(conn) is not None
        assert await referee.leave(conn) is None
        assert len(recorded) == 1

    asyncio.run(go())


def test_a_store_failure_does_not_break_the_disconnect(
    referee: HassaultReferee, monkeypatch
):
    """The same guard `hub.py` puts around its own result write: a database
    hiccup must not leave a room holding a player who is not there."""

    def boom(*a, **k):
        raise RuntimeError("store is down")

    monkeypatch.setattr(store, "record_result", boom)

    async def go():
        conn = seat()
        await referee.join(conn, "hd_pit")
        assert await referee.leave(conn) is not None
        assert referee.server.player_for(conn) is None

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Over the wire
# ---------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    """The real app, with dev auth on and its own data dir."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GAMES_ALLOW_DEV_AUTH", "1")
    from backend.games_server.app import app

    return TestClient(app)


def test_the_map_list_is_served(server):
    assert server.get("/hassault/maps").json()["maps"] == [
        "hd_atrium",
        "hd_crossing",
        "hd_pit",
    ]


def test_an_unauthenticated_socket_never_reaches_a_room(server):
    """Closed before `accept` where possible: a socket with no account has
    nothing to record a result against, and a rated room is the last place to
    discover that."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with server.websocket_connect("/hassault-ws?token=") as ws:
            ws.receive_text()


def test_a_join_over_the_wire_is_welcomed_as_rated(server):
    with server.websocket_connect("/hassault-ws?token=acct-rob") as ws:
        ws.send_text(
            json.dumps(
                {
                    "channel": "hassault",
                    "event": "join",
                    # The name on the wire is ignored — identity is the token —
                    # exactly as `channel.py` ignores it on a node.
                    "data": {"map": "hd_pit", "name": "imposter"},
                }
            )
        )
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "welcome"
        assert msg["data"]["rated"] is True
        assert msg["data"]["map"] == "hd_pit"


def test_the_wire_refuses_a_map_only_one_player_has(server):
    with server.websocket_connect("/hassault-ws?token=acct-rob") as ws:
        ws.send_text(
            json.dumps(
                {"channel": "hassault", "event": "join", "data": {"map": "ac_desert"}}
            )
        )
        for _ in range(8):
            msg = json.loads(ws.receive_text())
            if msg["event"] == "error":
                assert msg["data"]["code"] == "join_refused"
                assert "bundled" in msg["data"]["message"]
                return
        raise AssertionError("the server never refused an install-only map")


def test_traffic_on_another_channel_is_not_ours(server):
    """The envelope carries a channel because a node's socket is shared. This one
    is not, but speaking the same wire means the same rule — and a client that
    multiplexes must not be able to drive a match by accident."""
    with server.websocket_connect("/hassault-ws?token=acct-rob") as ws:
        ws.send_text(json.dumps({"channel": "telemetry", "event": "join", "data": {}}))
        ws.send_text(
            json.dumps(
                {"channel": "hassault", "event": "join", "data": {"map": "hd_pit"}}
            )
        )
        assert json.loads(ws.receive_text())["event"] == "welcome"
