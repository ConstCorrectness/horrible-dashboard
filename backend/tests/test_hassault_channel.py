"""The `hassault` `/ws` channel's sign-in gate.

HorribleAssault is an account game: you cannot play without one. The gate lives on
the join handler rather than on a route, because joining is what puts a body in a
match, and it covers **both** ways in — a local match and a match hosted on a
friend's node. The remote branch returns before `match_server.join` is ever
reached, so a check placed next to that call would gate nothing for cross-node
play; that asymmetry is the whole reason this file exists.

The other rule pinned here: the callsign comes from the account, and `data["name"]`
from the client is ignored entirely. A name anyone can type is not an identity.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.hassault import channel, fabric
from backend.modules.hassault.match import MatchRoom, match_server
from backend.modules.hassault.physics import flat_world


class Spawn:
    def __init__(self, x: float, y: float, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = 0.0
        self.yaw = 0.0
        self.attr2 = team


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m.get("event") == name]


@pytest.fixture(autouse=True)
def clean_state():
    """The match server is a process-global singleton by design."""
    yield
    match_server.rooms.clear()
    match_server.membership.clear()
    fabric._hosted.clear()
    fabric._remote.clear()
    fabric._invites.clear()


@pytest.fixture
def signed_out(monkeypatch):
    monkeypatch.setattr(channel, "_signed_in_callsign", lambda: None)


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setattr(channel, "_signed_in_callsign", lambda: "ada-prime")


def make_room(room_id: str = "r1") -> MatchRoom:
    room = MatchRoom(
        room_id, "testmap", flat_world(32), [Spawn(8, 8), Spawn(20, 20, 1)]
    )
    match_server.rooms[room_id] = room
    return room


def join(conn: FakeConn, **data: Any) -> None:
    asyncio.run(channel.handle(conn, {"event": "join", "data": data}))


# ---------------------------------------------------------------------------
# Signed out: neither way in works
# ---------------------------------------------------------------------------


def test_local_join_is_refused_when_signed_out(signed_out):
    make_room()
    conn = FakeConn()
    join(conn, map="testmap", name="whoever", room="r1")

    errors = conn.events("error")
    assert errors and errors[0]["code"] == "not_signed_in"
    assert not conn.events("welcome")
    assert match_server.rooms["r1"].players == {}


def test_remote_join_is_refused_when_signed_out(signed_out, monkeypatch):
    """The branch that returns early. A gate placed next to `match_server.join`
    would let this one straight through to a friend's machine."""
    reached: list[Any] = []
    monkeypatch.setattr(
        fabric, "bind_remote", lambda *a, **k: reached.append(a) or "binding"
    )

    conn = FakeConn()
    join(conn, map="testmap", name="whoever", room="r1", host="friend-node")

    errors = conn.events("error")
    assert errors and errors[0]["code"] == "not_signed_in"
    # Nothing was bound and nothing went to the peer — we never got that far.
    assert reached == []


def test_a_refused_join_does_not_evict_an_existing_player(signed_in, monkeypatch):
    """The check sits above `_leave_any`. Signing out mid-session must not mean the
    next stray join kicks you out of the match you are already in."""
    make_room()
    conn = FakeConn()
    join(conn, map="testmap", room="r1")
    assert len(match_server.rooms["r1"].players) == 1

    monkeypatch.setattr(channel, "_signed_in_callsign", lambda: None)
    join(conn, map="testmap", room="r1")

    assert len(match_server.rooms["r1"].players) == 1


# ---------------------------------------------------------------------------
# Signed in: the account's callsign is the identity
# ---------------------------------------------------------------------------


def test_join_uses_the_account_callsign_not_the_client_supplied_name(signed_in):
    make_room()
    conn = FakeConn()
    join(conn, map="testmap", name="i-am-somebody-else", room="r1")

    welcome = conn.events("welcome")
    assert welcome
    player = next(iter(match_server.rooms["r1"].players.values()))
    assert player.name == "ada-prime"


def test_remote_join_forwards_the_account_callsign(signed_in, monkeypatch):
    forwarded: list[str] = []
    monkeypatch.setattr(fabric, "bind_remote", lambda *a, **k: "binding")

    async def fake_send(binding: Any, name: str) -> None:
        forwarded.append(name)

    monkeypatch.setattr(fabric, "send_remote_join", fake_send)

    conn = FakeConn()
    join(conn, map="testmap", name="spoofed", room="r1", host="friend-node")

    assert forwarded == ["ada-prime"]


def test_signed_in_without_a_callsign_is_still_refused(monkeypatch):
    """Enlistment is the second half of signing up: an account with no callsign has
    no name to play under, so it is not yet allowed in."""
    from backend.modules.games import server_auth

    monkeypatch.setattr(
        server_auth,
        "signed_in_account",
        lambda: {"id": "local:1", "display_name": "ada", "handle": None},
    )
    make_room()
    conn = FakeConn()
    join(conn, map="testmap", room="r1")

    errors = conn.events("error")
    assert errors and errors[0]["code"] == "not_signed_in"
    assert match_server.rooms["r1"].players == {}
