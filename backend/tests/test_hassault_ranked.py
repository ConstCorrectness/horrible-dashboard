"""The ranked relay: a client reaching the game server's referee through its node.

The client speaks one protocol — the node's `hassault` channel — and a single flag
on the join decides where the room is. These tests pin the parts that are easy to
get subtly wrong and impossible to notice:

- that a ranked join does **not** open a room on this node (which would look
  identical to the player and count for nothing),
- that input is forwarded rather than re-validated here (a second validator with
  no authority behind it is where a gap appears),
- and that the result is recorded as `server` **because the server said so**,
  never because the node knew it was in a ranked room.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.modules.hassault import channel, ranked, results
from backend.modules.hassault.match import match_server


class FakeClient:
    """A browser's `/ws`, as the channel sees it."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self) -> list[str]:
        return [m.get("event") for m in self.sent]


class FakeServerSocket:
    """The game server's socket, scripted.

    `queue` is what the server will say; `sent` is what the node said to it.
    """

    def __init__(self, script: list[dict] | None = None) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self._queue: asyncio.Queue = asyncio.Queue()
        for msg in script or []:
            self._queue.put_nowait(msg)

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        self.closed = True

    def push(self, msg: dict) -> None:
        self._queue.put_nowait(msg)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        msg = await self._queue.get()
        if msg is None:
            raise StopAsyncIteration
        return json.dumps(msg)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Own data dir (the results DB) and no real network."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "backend.modules.games.server_auth._play_token", lambda: "test-token"
    )
    monkeypatch.setattr(
        "backend.modules.games.server_auth.signed_in_account",
        lambda: {"account_id": "acct-rob"},
    )
    # The join path refuses a node with no username before it ever reaches the
    # ranked branch — identity is the account's, never the wire's.
    monkeypatch.setattr(
        "backend.modules.hassault.channel._signed_in_username", lambda: "rob"
    )
    # `leave` waits for the server's parting result, which is right in a game and
    # pointless in a test: nothing here asserts on the duration, and eight tests
    # sitting through a two-second window is sixteen seconds of nothing.
    monkeypatch.setattr(ranked, "RESULT_WAIT", 0.05)
    yield
    ranked._sessions.clear()


@pytest.fixture
def fake_server(monkeypatch) -> FakeServerSocket:
    socket = FakeServerSocket()

    async def fake_connect(url, **kw):
        socket.url = url
        return socket

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect)
    return socket


def join_msg(**data) -> dict:
    return {"event": "join", "data": {"map": "hd_pit", "name": "rob", **data}}


# ---------------------------------------------------------------------------
# Where the room is
# ---------------------------------------------------------------------------


def test_a_ranked_join_opens_no_room_on_this_node(fake_server: FakeServerSocket):
    """The failure this prevents is invisible: a ranked match that quietly ran
    locally would look identical to the player and count for nothing."""
    client = FakeClient()

    async def go():
        await channel.handle(client, join_msg(ranked=True))
        assert match_server.player_for(client) is None
        assert ranked.session_for(client) is not None
        # And the join was passed up to the server.
        assert fake_server.sent[0]["event"] == "join"
        assert fake_server.sent[0]["data"]["map"] == "hd_pit"
        await ranked.leave(client)

    asyncio.run(go())


def test_the_token_travels_in_the_url_not_to_the_client(
    fake_server: FakeServerSocket,
):
    """The node's game-server JWT is the node's. A client that had to open its own
    socket would need a copy of it."""
    client = FakeClient()

    async def go():
        await channel.handle(client, join_msg(ranked=True))
        assert "token=test-token" in fake_server.url
        assert not any("test-token" in json.dumps(m) for m in client.sent)
        await ranked.leave(client)

    asyncio.run(go())


def test_an_unreachable_server_is_an_error_not_a_dead_socket(monkeypatch):
    """A ranked join that fails should leave the player in the menu with a reason.
    Raising here would close the socket their whole session runs on."""

    async def refuse(url, **kw):
        raise OSError("no route to host")

    monkeypatch.setattr("websockets.asyncio.client.connect", refuse)
    client = FakeClient()

    async def go():
        await channel.handle(client, join_msg(ranked=True))
        assert client.sent, "the client was told nothing"
        last = client.sent[-1]
        assert last["event"] == "error"
        assert last["data"]["code"] == "ranked_unreachable"
        assert ranked.session_for(client) is None

    asyncio.run(go())


def test_a_plain_join_still_opens_a_local_room(monkeypatch):
    """The flag is the only difference. Casual play must not have moved."""
    client = FakeClient()

    async def go():
        await channel.handle(client, join_msg())
        assert match_server.player_for(client) is not None
        assert ranked.session_for(client) is None
        await match_server.leave(client)

    asyncio.run(go())


# ---------------------------------------------------------------------------
# What flows through
# ---------------------------------------------------------------------------


def test_the_servers_frames_reach_the_client_verbatim(fake_server: FakeServerSocket):
    """A snapshot reshaped on the way through is a second implementation of the
    wire, and the client already knows how to read the original."""
    client = FakeClient()

    async def go():
        await channel.handle(client, join_msg(ranked=True))
        snapshot = {
            "channel": "hassault",
            "event": "snapshot",
            "data": {"tick": 7, "ack": 3, "players": []},
        }
        fake_server.push(snapshot)
        await asyncio.sleep(0.05)
        assert snapshot in client.sent
        await ranked.leave(client)

    asyncio.run(go())


def test_input_is_forwarded_untouched(fake_server: FakeServerSocket):
    """Validated by the server, because the server is the one being lied to."""
    client = FakeClient()

    async def go():
        await channel.handle(client, join_msg(ranked=True))
        commands = [{"seq": 1, "forward": 50, "dt": 0.016}]
        await channel.handle(client, {"event": "input", "data": {"commands": commands}})
        await asyncio.sleep(0.01)
        relayed = [m for m in fake_server.sent if m["event"] == "input"]
        assert relayed, "nothing was forwarded"
        # Untouched: the 50 goes up as a 50 and the *server* clamps it.
        assert relayed[0]["data"]["commands"] == commands
        await ranked.leave(client)

    asyncio.run(go())


def test_bots_are_refused_in_a_ranked_room(fake_server: FakeServerSocket):
    """A match whose roster a player can reshape is not one their result should
    count for."""
    client = FakeClient()

    async def go():
        await channel.handle(client, join_msg(ranked=True))
        await channel.handle(client, {"event": "add_bot", "data": {"count": 3}})
        assert client.sent[-1]["event"] == "error"
        assert not any(m["event"] == "add_bot" for m in fake_server.sent)
        await ranked.leave(client)

    asyncio.run(go())


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------


def test_a_served_result_is_recorded_as_the_servers(fake_server: FakeServerSocket):
    client = FakeClient()

    async def go():
        await channel.handle(client, join_msg(ranked=True))
        fake_server.push(
            {
                "channel": "hassault",
                "event": "result",
                "data": {
                    "map": "hd_pit",
                    "room": "r1",
                    "name": "rob",
                    "kills": 6,
                    "deaths": 2,
                    "headKills": 3,
                    "damageDealt": 900,
                    "won": True,
                    "mvp": True,
                    "opponents": 3,
                    "authority": "server",
                    "playedAt": 1000.0,
                },
            }
        )
        await asyncio.sleep(0.05)
        row = results.latest("acct-rob")
        assert row is not None
        assert row["kills"] == 6
        # The whole point of the exercise.
        assert row["authority"] == "server"
        assert row["rated"] is True
        await ranked.leave(client)

    asyncio.run(go())


def test_nothing_is_recorded_locally_for_a_ranked_seat(fake_server: FakeServerSocket):
    """Leaving a ranked room must not also file a local row: the node computed
    nothing, and a second `local` copy of the same match would be a self-reported
    row sitting beside the real one."""
    client = FakeClient()

    async def go():
        await channel.handle(client, join_msg(ranked=True))
        await channel.handle(client, {"event": "leave", "data": {}})
        await asyncio.sleep(0.05)
        assert results.latest("acct-rob") is None
        assert ranked.session_for(client) is None

    asyncio.run(go())


def test_the_endpoint_follows_the_server_the_node_signed_in_to(monkeypatch):
    """Two URLs that must agree is two URLs that can disagree — and the failure
    reads as 'invalid token' rather than as a misconfiguration."""
    monkeypatch.setattr(
        "backend.modules.games.client.resolve_server_url",
        lambda: "https://horrible-games.fly.dev",
    )
    assert ranked.server_ws_url() == "wss://horrible-games.fly.dev/hassault-ws"

    monkeypatch.setattr(
        "backend.modules.games.client.resolve_server_url",
        lambda: "http://127.0.0.1:9200/",
    )
    assert ranked.server_ws_url() == "ws://127.0.0.1:9200/hassault-ws"
