"""Lobbies: a group is made first, then matched as one.

The property that matters is the last one in the file: whoever is in the lobby
when the leader presses play is sent to the same room, and that room has a seat
held for each of them.
"""

from __future__ import annotations

import pytest

from backend.games_server import hassault_lobby, hassault_rooms
from backend.games_server.hassault_lobby import LobbyBook, LobbyError
from backend.games_server.hassault_rooms import HassaultReferee


@pytest.fixture
def book() -> LobbyBook:
    return LobbyBook()


@pytest.fixture
def referee(tmp_path, monkeypatch) -> HassaultReferee:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return HassaultReferee()


def test_a_lobby_has_a_code_a_leader_and_tokens(book: LobbyBook):
    made = book.create("  Sable  ")
    code = made["lobby"]["code"]
    assert len(code) == hassault_lobby.CODE_LENGTH
    assert set(code) <= set(hassault_lobby.CODE_ALPHABET)
    assert made["lobby"]["leader"] is True
    assert made["lobby"]["members"] == [{"name": "Sable", "leader": True, "you": True}]

    joined = book.join(code.lower(), "Ghost")  # typed in any case
    assert joined["token"] != made["token"]
    assert joined["lobby"]["leader"] is False
    assert [m["name"] for m in joined["lobby"]["members"]] == ["Sable", "Ghost"]
    # Each sees who they are, and nobody's token is in the payload.
    assert [m["you"] for m in book.state(code, joined["token"])["members"]] == [
        False,
        True,
    ]
    assert "token" not in str(book.state(code, made["token"]))


def test_what_a_lobby_refuses(book: LobbyBook):
    with pytest.raises(LobbyError):
        book.join("ZZZZZ", "Ghost")
    made = book.create("Sable")
    code = made["lobby"]["code"]
    with pytest.raises(LobbyError):
        book.state(code, "not-a-token")
    for n in range(hassault_lobby.MAX_MEMBERS - 1):
        book.join(code, f"m{n}")
    with pytest.raises(LobbyError, match="full"):
        book.join(code, "one-too-many")


def test_leaving_passes_the_lead_and_the_last_one_out_closes_it(book: LobbyBook):
    a = book.create("Sable")
    code = a["lobby"]["code"]
    b = book.join(code, "Ghost")
    assert book.leave(code, a["token"]) is True
    assert book.state(code, b["token"])["leader"] is True
    assert book.leave(code, a["token"]) is False  # already gone
    assert book.leave(code, b["token"]) is True
    with pytest.raises(LobbyError):
        book.join(code, "Late")


def test_a_member_who_stops_asking_is_gone(book: LobbyBook, monkeypatch):
    a = book.create("Sable")
    code = a["lobby"]["code"]
    b = book.join(code, "Ghost")
    clock = hassault_lobby.time.monotonic()
    monkeypatch.setattr(
        hassault_lobby.time,
        "monotonic",
        lambda: clock + hassault_lobby.MEMBER_SILENCE_S + 1,
    )
    # Ghost asks; Sable has closed the game. Ghost leads what is left.
    state = book.state(code, b["token"])
    assert [m["name"] for m in state["members"]] == ["Ghost"]
    assert state["leader"] is True


def test_only_the_leader_starts_the_search(book: LobbyBook, referee: HassaultReferee):
    a = book.create("Sable")
    code = a["lobby"]["code"]
    b = book.join(code, "Ghost")
    with pytest.raises(LobbyError, match="Sable"):
        book.play(code, b["token"], referee.find_match)
    assert not referee.server.rooms


def test_the_whole_lobby_is_sent_to_one_room_with_a_seat_each(
    book: LobbyBook, referee: HassaultReferee
):
    a = book.create("Sable")
    code = a["lobby"]["code"]
    b = book.join(code, "Ghost")
    c = book.join(code, "Wren")

    state = book.play(code, a["token"], referee.find_match, maps=["hd_pit"], mode="tdm")
    match = state["match"]
    assert match["map"] == "hd_pit" and match["mode"] == "tdm" and match["opened"]
    assert state["serial"] == 1
    # Everyone is told the same room, and what was picked.
    for member in (b, c):
        seen = book.state(code, member["token"])
        assert seen["match"] == match and seen["serial"] == 1
        assert seen["maps"] == ["hd_pit"] and seen["mode"] == "tdm"
    # Three seats are promised there, not one.
    assert referee._holds(match["id"]) == 3

    # A second search is a new serial, so it is acted on again.
    again = book.play(code, a["token"], referee.find_match, maps=["hd_pit"], mode="tdm")
    assert again["serial"] == 2
    assert again["match"]["id"] == match["id"]


def test_a_lobby_does_not_fit_in_a_room_without_seats_for_all(
    referee: HassaultReferee, monkeypatch
):
    monkeypatch.setattr(hassault_rooms, "MAX_PLAYERS", 4)
    lone = referee.find_match("hd_pit")
    referee.find_match("hd_pit")
    # Two seats are promised in a room of four: a party of three needs its own.
    party = referee.find_match("hd_pit", seats=3)
    assert party["id"] != lone["id"]
    assert referee._holds(party["id"]) == 3


def test_several_maps_narrow_a_search_to_any_of_them(referee: HassaultReferee):
    import asyncio

    from backend.tests.test_games_hassault_matchmake import seat

    async def go():
        welcome = await referee.join(seat("alice"), "hd_dust2")
        # Dust2 is one of the two picked, and somebody is on it.
        found = referee.find_match(maps=["hd_pit", "hd_dust2"])
        assert found["id"] == welcome["room"]
        # Neither picked map has anyone: the first picked is opened.
        fresh = referee.find_match(maps=["hd_mirage", "hd_pit"])
        assert fresh["opened"] and fresh["map"] == "hd_mirage"
        # A picked map that cannot host the game is passed over for one that can.
        aim = next(m for m in referee.maps() if "defuse" not in referee.map_modes(m))
        site = next(m for m in referee.maps() if "defuse" in referee.map_modes(m))
        assert referee.find_match(maps=[aim, site], mode="defuse")["map"] == site
        with pytest.raises(ValueError):
            referee.find_match(maps=[aim], mode="defuse")
        with pytest.raises(ValueError):
            referee.find_match(maps=["hd_pit", "nope"])

    asyncio.run(go())


@pytest.fixture
def server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server.app import app

    hassault_rooms.referee.server.rooms.clear()
    hassault_rooms.referee._held.clear()
    hassault_rooms.referee._unclaimed.clear()
    hassault_lobby.book._lobbies.clear()
    return TestClient(app)


def test_the_lobby_over_http_from_open_to_play(server):
    made = server.post("/api/hassault/lobbies", json={"name": "Sable"}).json()
    code, lead = made["lobby"]["code"], made["token"]
    joined = server.post(f"/api/hassault/lobbies/{code}/join", json={"name": "Ghost"})
    assert joined.status_code == 200
    guest = joined.json()["token"]

    refused = server.post(
        f"/api/hassault/lobbies/{code}/play", json={"token": guest, "maps": ["hd_pit"]}
    )
    assert refused.status_code == 409 and "Sable" in refused.json()["detail"]

    played = server.post(
        f"/api/hassault/lobbies/{code}/play", json={"token": lead, "maps": ["hd_pit"]}
    )
    assert played.status_code == 200, played.text
    room = played.json()["match"]["id"]
    seen = server.get(f"/api/hassault/lobbies/{code}", params={"token": guest}).json()
    assert seen["match"]["id"] == room and seen["serial"] == 1

    assert server.post(
        f"/api/hassault/lobbies/{code}/leave", json={"token": guest}
    ).json() == {"left": True}
    assert (
        server.get(f"/api/hassault/lobbies/{code}", params={"token": guest}).status_code
        == 409
    )
    assert server.post("/api/hassault/lobbies/NOPE1/join", json={}).status_code == 409


def test_a_search_over_http_takes_several_maps(server):
    res = server.post(
        "/api/hassault/matchmake", json={"maps": ["hd_pit", "hd_dust2"], "mode": "dm"}
    )
    assert res.status_code == 200 and res.json()["map"] == "hd_pit"
    assert (
        server.post("/api/hassault/matchmake", json={"maps": "hd_pit"}).status_code
        == 200
    )  # not a list: ignored, a search for anything
