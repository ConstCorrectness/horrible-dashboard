"""Finding a match is one rule, and the server holds it.

The browser's Quick Play and the native menu's Find Match used to read the room
list and each choose for itself — on different default maps — so two people
searching an empty server at the same moment got a room each. These tests are
about the property that fixes that: whoever asks, and however close together,
the answers converge on one room.
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


def seat(name: str) -> SeatConn:
    return SeatConn(FakeSocket(), f"acct-{name}", name)


@pytest.fixture
def referee(tmp_path, monkeypatch) -> HassaultReferee:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return HassaultReferee()


def test_two_searches_on_an_empty_server_get_the_same_room(referee: HassaultReferee):
    """The race the rule exists for: nobody has joined the first room yet."""

    async def go():
        first = referee.find_match()
        second = referee.find_match()
        assert first["opened"] is True
        assert second["opened"] is False
        assert first["id"] == second["id"]
        assert first["map"] == hassault_rooms.quick_map()

    asyncio.run(go())


def test_a_search_joins_where_people_are_rather_than_opening(referee: HassaultReferee):
    async def go():
        welcome = await referee.join(seat("alice"), "hd_pit")
        found = referee.find_match()
        assert found["id"] == welcome["room"]
        assert found["map"] == "hd_pit"
        assert found["opened"] is False
        assert found["humans"] == 1

    asyncio.run(go())


def test_the_fuller_room_wins(referee: HassaultReferee):
    async def go():
        quiet = await referee.join(seat("alice"), "hd_pit")
        busy = await referee.join(seat("bob"), "hd_pit", new=True)
        await referee.join(seat("carol"), "hd_pit", busy["room"])
        assert quiet["room"] != busy["room"]
        assert referee.find_match()["id"] == busy["room"]

    asyncio.run(go())


def test_a_map_or_a_mode_narrows_the_search(referee: HassaultReferee):
    async def go():
        await referee.join(seat("alice"), "hd_pit")
        elsewhere = referee.find_match("hd_dust2")
        assert elsewhere["opened"] is True
        assert elsewhere["map"] == "hd_dust2"
        # A game the running room is not playing opens one that is.
        tdm = referee.find_match("hd_pit", "tdm")
        assert tdm["opened"] is True
        assert tdm["mode"] == "tdm"

    asyncio.run(go())


def test_a_search_with_only_a_mode_opens_on_a_map_that_offers_it(
    referee: HassaultReferee,
):
    async def go():
        found = referee.find_match(mode="defuse")
        assert found["mode"] == "defuse"
        assert "defuse" in referee.map_modes(found["map"])

    asyncio.run(go())


def test_what_cannot_be_played_is_refused_before_a_room_exists(
    referee: HassaultReferee,
):
    async def go():
        with pytest.raises(ValueError):
            referee.find_match("not_a_map")
        with pytest.raises(ValueError):
            referee.find_match(mode="duel")  # paired by rating, never found
        with pytest.raises(ValueError):
            referee.find_match(mode="nonsense")
        assert not referee.server.rooms

    asyncio.run(go())


def test_a_room_everyone_left_is_not_a_match(referee: HassaultReferee):
    async def go():
        alice = seat("alice")
        welcome = await referee.join(alice, "hd_pit")
        await referee.leave(alice)
        assert welcome["room"] in referee.server.rooms, "the grace period is the point"
        found = referee.find_match("hd_pit")
        assert found["opened"] is True
        assert found["id"] != welcome["room"]

    asyncio.run(go())


def test_a_promised_seat_lapses(referee: HassaultReferee, monkeypatch):
    async def go():
        first = referee.find_match()
        clock = hassault_rooms.time.monotonic()
        monkeypatch.setattr(
            hassault_rooms.time,
            "monotonic",
            lambda: clock + hassault_rooms.MATCH_HOLD_S + 1,
        )
        # Nobody came: the room is empty again, and a search opens another.
        second = referee.find_match()
        assert second["opened"] is True
        assert second["id"] != first["id"]

    asyncio.run(go())


def test_promised_seats_count_toward_a_full_room(referee: HassaultReferee, monkeypatch):
    monkeypatch.setattr(hassault_rooms, "MAX_PLAYERS", 2)

    async def go():
        first = referee.find_match()
        second = referee.find_match()
        third = referee.find_match()
        assert first["id"] == second["id"]
        assert third["id"] != first["id"], "a third seat was promised in a room of two"

    asyncio.run(go())


def test_the_first_to_arrive_owns_a_room_opened_for_a_search(referee: HassaultReferee):
    async def go():
        found = referee.find_match()
        alice, bob = seat("alice"), seat("bob")
        a = await referee.join(alice, found["map"], found["id"])
        b = await referee.join(bob, found["map"], found["id"])
        assert a["room"] == b["room"] == found["id"]
        assert referee.creators[found["id"]] == alice.account_id

    asyncio.run(go())


def test_empty_rooms_cannot_be_opened_without_limit(referee: HassaultReferee):
    async def go():
        maps = referee.maps()
        assert len(maps) > hassault_rooms.MAX_UNCLAIMED_ROOMS
        for name in maps[: hassault_rooms.MAX_UNCLAIMED_ROOMS]:
            assert referee.find_match(name)["opened"] is True
        with pytest.raises(ValueError):
            referee.find_match(maps[hassault_rooms.MAX_UNCLAIMED_ROOMS])
        # A search that fits a room already opening is still answered.
        assert referee.find_match(maps[0])["opened"] is False

    asyncio.run(go())


@pytest.fixture
def server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GAMES_ALLOW_DEV_AUTH", "1")
    from backend.games_server.app import app

    hassault_rooms.referee.server.rooms.clear()
    hassault_rooms.referee._held.clear()
    hassault_rooms.referee._unclaimed.clear()
    return TestClient(app)


def test_the_route_answers_a_guest_and_the_join_lands_there(server):
    res = server.post("/api/hassault/matchmake", json={"map": "hd_pit"})
    assert res.status_code == 200
    found = res.json()
    assert found["map"] == "hd_pit" and found["opened"] is True

    again = server.post("/api/hassault/matchmake", json={})
    assert again.json()["id"] == found["id"]

    with server.websocket_connect("/hassault-ws?guest=1&name=Finder") as ws:
        ws.send_text(
            json.dumps(
                {
                    "channel": "hassault",
                    "event": "join",
                    "data": {"map": found["map"], "room": found["id"]},
                }
            )
        )
        welcome = json.loads(ws.receive_text())
        assert welcome["event"] == "welcome"
        assert welcome["data"]["room"] == found["id"]


def test_a_room_can_be_looked_into_without_joining_it(server):
    assert server.get("/api/hassault/rooms/nope").status_code == 404
    with server.websocket_connect("/hassault-ws?guest=1&name=Host") as ws:
        ws.send_text(
            json.dumps(
                {"channel": "hassault", "event": "join", "data": {"map": "hd_pit"}}
            )
        )
        welcome = json.loads(ws.receive_text())["data"]
        detail = server.get(f"/api/hassault/rooms/{welcome['room']}").json()
        assert detail["map"] == "hd_pit"
        assert detail["humans"] == 1
        assert detail["host"] == welcome["playerId"]
        assert [(r["name"], r["bot"]) for r in detail["roster"]] == [("Host", False)]
        # The scoreboard's columns and nothing a wall hack would want.
        assert set(detail["roster"][0]) == {
            "id",
            "name",
            "team",
            "kills",
            "deaths",
            "bot",
        }


def test_the_route_refuses_with_a_reason(server):
    res = server.post("/api/hassault/matchmake", json={"map": "not_a_map"})
    assert res.status_code == 409
    assert "not a bundled map" in res.json()["detail"]
    # No body at all is a search for anything.
    assert server.post("/api/hassault/matchmake").status_code == 200
