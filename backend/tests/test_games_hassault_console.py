"""The developer console on the game server: the room's creator or an admin, never
in a ranked match, and what it sets is what the simulation honours.

Each test runs in one `asyncio.run(go())`: the match server's tick task lives on
that loop, so the joins and the HTTP calls share it.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from backend.games_server import auth, hassault_console
from backend.games_server.hassault_rooms import HassaultReferee, SeatConn


class FakeSocket:
    async def send_text(self, raw: str) -> None:
        json.loads(raw)


def seat(name: str) -> SeatConn:
    return SeatConn(FakeSocket(), f"acct-{name}", name)


@pytest.fixture
def referee(tmp_path, monkeypatch) -> HassaultReferee:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HASSAULT_ADMINS", raising=False)
    ref = HassaultReferee()
    monkeypatch.setattr(hassault_console, "referee", ref)
    # A token is the account id it names, so a test can be anyone.
    monkeypatch.setattr(
        auth, "resolve_token", lambda t: {"account_id": t, "display_name": t}
    )
    return ref


def run(referee: HassaultReferee, body) -> None:
    app = FastAPI()
    app.include_router(hassault_console.router)

    async def go() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:

            async def post(token: str | None, command: str, room_id: str | None):
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                return await client.post(
                    "/api/hassault/console/exec",
                    json={"command": command, "room_id": room_id},
                    headers=headers,
                )

            try:
                await body(post)
            finally:
                await referee.shutdown()

    asyncio.run(go())


def player_of(room, account: str):
    return next(p for p in room.humans if p.conn.account_id == account)


def test_the_creator_turns_god_mode_on_for_the_simulation(referee):
    async def body(post) -> None:
        alice = seat("alice")
        await referee.join(alice, "hd_assault", new=True)
        room_id = next(iter(referee.server.rooms))
        res = await post("acct-alice", "player.god 1", room_id)
        assert res.status_code == 200, res.text
        assert res.json()["affected_cvars"] == {"player.god": True}
        room = referee.server.get(room_id)
        assert player_of(room, "acct-alice").god is True

    run(referee, body)


def test_another_player_is_refused_with_a_403(referee):
    async def body(post) -> None:
        await referee.join(seat("alice"), "hd_assault", new=True)
        room_id = next(iter(referee.server.rooms))
        await referee.join(seat("bob"), "hd_assault", room_id=room_id)
        res = await post("acct-bob", "player.god 1", room_id)
        assert res.status_code == 403
        assert "creator" in res.json()["detail"]
        assert player_of(referee.server.get(room_id), "acct-bob").god is False
        # Nor without signing in.
        assert (await post(None, "player.god 1", room_id)).status_code == 403

    run(referee, body)


def test_an_admin_may_use_it_in_someone_elses_room(referee, monkeypatch):
    monkeypatch.setenv("HASSAULT_ADMINS", "acct-bob")

    async def body(post) -> None:
        await referee.join(seat("alice"), "hd_assault", new=True)
        room_id = next(iter(referee.server.rooms))
        await referee.join(seat("bob"), "hd_assault", room_id=room_id)
        res = await post("acct-bob", "god", room_id)
        assert res.status_code == 200, res.text
        assert player_of(referee.server.get(room_id), "acct-bob").god is True

    run(referee, body)


def test_a_ranked_room_refuses_even_its_creator_and_admins(referee, monkeypatch):
    monkeypatch.setenv("HASSAULT_ADMINS", "acct-alice")

    async def body(post) -> None:
        room = referee.server.create(referee.duel_map(), mode="duel")
        referee.creators[room.id] = "acct-alice"
        res = await post("acct-alice", "player.god 1", room.id)
        assert res.status_code == 403
        assert "ranked" in res.json()["detail"]

    run(referee, body)


def test_unknown_cvars_are_a_clear_400_not_a_script(referee):
    async def body(post) -> None:
        await referee.join(seat("alice"), "hd_assault", new=True)
        room_id = next(iter(referee.server.rooms))
        res = await post("acct-alice", "__import__('os')", room_id)
        assert res.status_code == 400
        res = await post("acct-alice", "gameplay.damage_scale 2", room_id)
        assert res.status_code == 200
        assert referee.server.get(room_id).settings.damage_scale == 2.0

    run(referee, body)


def test_the_node_console_refuses_a_ranked_room_too(monkeypatch):
    """A node's own console has no token to check (its owner opened every room
    it runs), but a duel is still ranked there."""
    from backend.modules.hassault import routes
    from backend.modules.hassault.match import MatchServer

    server = MatchServer(record_replays=False)
    monkeypatch.setattr(routes, "match_server", server)
    room = server.create("hd_pit", mode="duel")
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post(
                "/api/hassault/console/exec",
                json={"command": "player.god 1", "room_id": room.id},
            )

    res = asyncio.run(go())
    assert res.status_code == 403
    assert "ranked" in res.json()["detail"]


def bot_names(room) -> list[str]:
    return [p.name for p in room.players.values() if p.is_bot]


def test_the_host_runs_the_bots_but_not_the_cheats(referee):
    """Alice opens the room and leaves; Bob, seated longest, is now its host."""

    async def body(post) -> None:
        alice, bob = seat("alice"), seat("bob")
        await referee.join(alice, "hd_assault", new=True)
        room_id = next(iter(referee.server.rooms))
        await referee.join(bob, "hd_assault", room_id=room_id)
        room = referee.server.get(room_id)
        assert (await post("acct-bob", "server.bots.add 1", room_id)).status_code == 403
        await referee.leave(alice)

        res = await post("acct-bob", 'server.bots.add(count=3, skill="hard")', room_id)
        assert res.status_code == 200, res.text
        assert len(bot_names(room)) == 3
        res = await post("acct-bob", "server.bots.remove 2", room_id)
        assert res.status_code == 200, res.text
        assert len(bot_names(room)) == 1
        res = await post("acct-bob", "server.bots.freeze 1", room_id)
        assert res.status_code == 200 and room.bots_frozen is True

        # The creator's powers do not pass to whoever is left holding the room.
        res = await post("acct-bob", "gameplay.damage_scale 50", room_id)
        assert res.status_code == 403
        assert "creator" in res.json()["detail"]
        assert room.settings.damage_scale == 1.0
        res = await post("acct-bob", "player.god 1", room_id)
        assert res.status_code == 403

        res = await post("acct-bob", "server.bots.kick_all()", room_id)
        assert res.status_code == 200 and bot_names(room) == []

    run(referee, body)


def test_the_creator_keeps_bots_after_losing_the_host_seat(referee):
    async def body(post) -> None:
        bob, alice = seat("bob"), seat("alice")
        await referee.join(alice, "hd_assault", new=True)
        room_id = next(iter(referee.server.rooms))
        room = referee.server.get(room_id)
        # Bob seated first in effect: Alice steps out and back in behind him.
        await referee.join(bob, "hd_assault", room_id=room_id)
        await referee.leave(alice)
        await referee.join(alice, "hd_assault", room_id=room_id)
        assert referee.host_of(room) == player_of(room, "acct-bob").id
        res = await post("acct-alice", "server.bots.add 2 easy", room_id)
        assert res.status_code == 200, res.text
        assert len(bot_names(room)) == 2

    run(referee, body)


def test_an_admin_named_by_handle_may_do_everything(referee, monkeypatch):
    from backend.games_server import store

    store.init_db()
    admin_id = store.upsert_account("github", "1", "ConstCorrectness")
    store.set_handle(admin_id, "constcorrectness")
    monkeypatch.setenv("HASSAULT_ADMINS", "@ConstCorrectness")

    async def body(post) -> None:
        alice = seat("alice")
        await referee.join(alice, "hd_assault", new=True)
        room_id = next(iter(referee.server.rooms))
        room = referee.server.get(room_id)
        # Not seated, not the creator: an admin acts on any room.
        res = await post(admin_id, "server.bots.add 2", room_id)
        assert res.status_code == 200, res.text
        res = await post(admin_id, "gameplay.damage_scale 2", room_id)
        assert res.status_code == 200 and room.settings.damage_scale == 2.0
        res = await post(admin_id, "help", room_id)
        assert res.json()["result_data"] == {"role": "admin"}
        assert any("server.kick" in line for line in res.json()["output"])

        # Kicking is the admin's alone: not even the creator may.
        res = await post("acct-alice", "server.kick alice", room_id)
        assert res.status_code == 403
        assert "admin" in res.json()["detail"]
        res = await post(admin_id, "server.kick alice", room_id)
        assert res.status_code == 200, res.text
        assert not room.humans

    run(referee, body)


def test_a_call_is_parsed_never_evaluated(referee):
    async def body(post) -> None:
        await referee.join(seat("alice"), "hd_assault", new=True)
        room_id = next(iter(referee.server.rooms))
        res = await post(
            "acct-alice", "server.bots.add(__import__('os').getpid())", room_id
        )
        assert res.status_code == 400
        assert "plain values" in res.json()["detail"]
        res = await post("acct-alice", "server.bots.add 1 godlike", room_id)
        assert res.status_code == 400
        assert not bot_names(referee.server.get(room_id))

    run(referee, body)


def test_a_seated_host_adds_bots_over_the_socket_even_as_a_guest(referee):
    """The menu's `add_bot`: checked against the socket's own account, so a
    guest hosting a room needs no token for it."""

    async def body(post) -> None:
        guest = SeatConn(FakeSocket(), "guest_1234", "Guest", is_guest=True)
        other = seat("bob")
        await referee.join(guest, "hd_assault", new=True)
        room_id = next(iter(referee.server.rooms))
        await referee.join(other, "hd_assault", room_id=room_id)
        room = referee.server.get(room_id)
        refused = await hassault_console.socket_bots(other, "add_bot", {"count": 2})
        assert refused and "host" in refused
        assert (
            await hassault_console.socket_bots(
                guest, "add_bot", {"count": 2, "skill": "hard"}
            )
            is None
        )
        assert len(bot_names(room)) == 2
        assert await hassault_console.socket_bots(guest, "remove_bot", {}) is None
        assert len(bot_names(room)) == 1

    run(referee, body)


def test_definitions_list_what_the_server_accepts(referee):
    app = FastAPI()
    app.include_router(hassault_console.router)

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.get("/api/hassault/console/definitions")

    data = asyncio.run(go()).json()
    names = {c["name"] for c in data["commands"]}
    assert {"server.bots.add", "server.bots.remove", "server.kick"} <= names
    assert {c["name"] for c in data["cvars"]} == set(hassault_console.GAMEPLAY_CVARS)
