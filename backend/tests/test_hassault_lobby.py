"""Lobbies: gathering a party before a match, locally and across the fabric.

The fabric is faked the same way `test_hassault_fabric.py` fakes it — what is
under test is the bridge, not the transport.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.hassault import channel, fabric
from backend.modules.hassault.lobby import LobbyError, lobby_server
from backend.modules.hassault.match import match_server
from backend.modules.network.models import PeerEnvelope


class FakePeerInfo:
    def __init__(self, node_id: str, trusted: bool = True) -> None:
        self.node_id = node_id
        self.node_name = f"node-{node_id}"
        self.trusted = trusted


class FakeSession:
    def __init__(self, node_id: str, trusted: bool = True) -> None:
        self.info = FakePeerInfo(node_id, trusted)


class FakeHub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def send_to(
        self, node_id: str, msg_type: str, data: dict[str, Any], re: str | None = None
    ) -> None:
        self.sent.append((node_id, msg_type, data))

    def messages(self, node_id: str) -> list[dict[str, Any]]:
        return [
            d["message"]
            for n, t, d in self.sent
            if n == node_id and t == fabric.HASSAULT_LOBBY_FRAME
        ]


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m.get("event") == name]

    def state(self) -> dict[str, Any]:
        return self.events("lobby")[-1]


def env(data: dict[str, Any]) -> PeerEnvelope:
    return PeerEnvelope(
        type="hassault_lobby", msg_id="m1", src="peer", ts=0.0, data=data
    )


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(channel, "_signed_in_username", lambda: "rob")
    yield
    lobby_server.lobbies.clear()
    lobby_server.membership.clear()
    match_server.rooms.clear()
    match_server.membership.clear()
    fabric._lobby_hosted.clear()
    fabric._lobby_remote.clear()
    fabric._invites.clear()


def test_a_lobby_gathers_chats_and_readies_without_starting_a_match():
    async def go():
        host, guest = FakeConn(), FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        await lobby_server.join(guest, lobby.id, "kim")
        await lobby_server.chat(guest, "  hi  ")
        await lobby_server.set_ready(guest, True)

        state = host.state()
        assert [m["name"] for m in state["members"]] == ["rob", "kim"]
        assert state["members"][1]["ready"] is True
        assert state["chat"][-1]["text"] == "hi"
        # Each member is told which row is them, and only the host holds the keys.
        assert host.state()["isHost"] and not guest.state()["isHost"]
        # Nothing about gathering a party opens a match.
        assert match_server.rooms == {}

    asyncio.run(go())


def test_start_opens_one_match_and_sends_everyone_to_it():
    async def go():
        host, guest = FakeConn(), FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        await lobby_server.join(guest, lobby.id, "kim")
        with pytest.raises(LobbyError):
            await lobby_server.start(guest)  # not the host

        room_id = await lobby_server.start(host)
        assert room_id in match_server.rooms
        for conn in (host, guest):
            start = conn.events("lobby_start")[-1]
            assert start["room"] == room_id and start["map"] == "hd_assault"
        # Starting again while that match runs reuses it — latecomers join the
        # same game rather than splitting the party.
        assert await lobby_server.start(host) == room_id
        # The lobby outlives the match it started.
        assert lobby_server.get(lobby.id) is not None

    asyncio.run(go())


def test_changing_the_map_unreadies_everyone_and_is_host_only():
    async def go():
        host, guest = FakeConn(), FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        await lobby_server.join(guest, lobby.id, "kim")
        await lobby_server.set_ready(guest, True)
        with pytest.raises(LobbyError):
            await lobby_server.configure(guest, "hd_pit", None)
        await lobby_server.configure(host, "hd_pit", None)
        state = guest.state()
        assert state["map"] == "hd_pit"
        assert not any(m["ready"] for m in state["members"])

    asyncio.run(go())


def test_the_host_leaving_closes_the_lobby_for_everyone():
    async def go():
        host, guest = FakeConn(), FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        await lobby_server.join(guest, lobby.id, "kim")
        await lobby_server.leave(host)
        assert guest.events("lobby_closed")
        assert lobby_server.get(lobby.id) is None
        assert lobby_server.lobby_for(guest) is None

    asyncio.run(go())


def test_a_peer_joins_a_hosted_lobby_and_is_tagged_with_its_node():
    async def go():
        host = FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        hub = FakeHub()
        await fabric.handle_lobby(
            hub,
            FakeSession("nodeA"),
            env({"op": "join", "client": "c1", "lobby": lobby.id, "name": "kim"}),
        )
        names = [m["name"] for m in host.state()["members"]]
        assert names == ["rob", "kim@nodeA"]
        assert host.state()["members"][1]["remote"] is True
        # The peer got the state, addressed to its browser.
        assert hub.messages("nodeA")[-1]["event"] == "lobby"

        await fabric.handle_lobby(
            hub,
            FakeSession("nodeA"),
            env({"op": "chat", "client": "c1", "lobby": lobby.id, "text": "gg"}),
        )
        assert host.state()["chat"][-1]["text"] == "gg"

        # A dropped peer takes its members with it.
        await fabric.drop_peer("nodeA")
        assert [m["name"] for m in host.state()["members"]] == ["rob"]

    asyncio.run(go())


def test_an_untrusted_peer_cannot_join_a_lobby():
    async def go():
        host = FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        await fabric.handle_lobby(
            FakeHub(),
            FakeSession("stranger", trusted=False),
            env({"op": "join", "client": "c1", "lobby": lobby.id, "name": "x"}),
        )
        assert len(lobby.members) == 1

    asyncio.run(go())


def test_the_guest_backend_stamps_the_host_node_on_lobby_frames():
    """A guest's pane joins the started match over the fabric, so it has to know
    which node the lobby lives on — the guest backend adds it, not the host."""

    async def go():
        browser = FakeConn()
        fabric.bind_remote_lobby(browser, "hostNode", "L1")
        binding = fabric.remote_lobby_for(browser)
        assert binding is not None
        await fabric.handle_lobby_frame(
            FakeHub(),
            FakeSession("hostNode"),
            PeerEnvelope(
                type=fabric.HASSAULT_LOBBY_FRAME,
                msg_id="m",
                src="hostNode",
                ts=0.0,
                data={
                    "client": binding.client,
                    "message": {
                        "channel": "hassault",
                        "event": "lobby_start",
                        "data": {"room": "r9", "map": "hd_pit"},
                    },
                },
            ),
        )
        assert browser.events("lobby_start")[-1]["host"] == "hostNode"

    asyncio.run(go())


def test_a_refused_remote_join_unbinds_the_browser():
    async def go():
        browser = FakeConn()
        binding = fabric.bind_remote_lobby(browser, "hostNode", "gone")
        await fabric.handle_lobby_frame(
            FakeHub(),
            FakeSession("hostNode"),
            PeerEnvelope(
                type=fabric.HASSAULT_LOBBY_FRAME,
                msg_id="m",
                src="hostNode",
                ts=0.0,
                data={
                    "client": binding.client,
                    "message": {
                        "channel": "hassault",
                        "event": "lobby_error",
                        "data": {"message": "that lobby has closed"},
                    },
                },
            ),
        )
        assert fabric.remote_lobby_for(browser) is None
        assert browser.events("lobby_error")

    asyncio.run(go())


def test_invite_opens_a_lobby_and_invites_to_it_in_one_message(monkeypatch):
    """The bug: the pane sent "host" then "invite" back to back, and the invite
    was dropped because the room did not exist yet. One message cannot race."""

    async def go():
        calls: list[tuple[str, str, str]] = []

        async def fake_invite(who: str, room_id: str, kind: str = "match"):
            calls.append((who, room_id, kind))
            return {"ok": True, "invited": who, "room": room_id, "kind": kind}

        monkeypatch.setattr(channel, "invite_friend", fake_invite)
        conn = FakeConn()
        await channel.handle(
            conn,  # type: ignore[arg-type]
            {"event": "lobby_invite", "data": {"who": "HD-XXXX", "map": "hd_assault"}},
        )
        entry = lobby_server.lobby_for(conn)
        assert entry is not None
        assert calls == [("HD-XXXX", entry[0].id, "lobby")]
        assert conn.events("invite_sent")
        assert match_server.rooms == {}  # no match was started

    asyncio.run(go())


def test_a_lobby_invite_is_received_as_a_lobby_invite(monkeypatch):
    async def go():
        async def no_notify(invite):
            return None

        monkeypatch.setattr(fabric, "_notify_invite", no_notify)
        await fabric.handle_invite(
            FakeHub(),
            FakeSession("hostNode"),
            PeerEnvelope(
                type=fabric.HASSAULT_INVITE,
                msg_id="m",
                src="hostNode",
                ts=0.0,
                data={"kind": "lobby", "room": "L1", "map": "hd_assault"},
            ),
        )
        [invite] = fabric.live_invites()
        assert invite["kind"] == "lobby" and invite["room"] == "L1"

    asyncio.run(go())


def test_voice_state_is_published_to_every_member():
    async def go():
        host, guest = FakeConn(), FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        await lobby_server.join(guest, lobby.id, "kim")
        await lobby_server.set_voice(guest, True, True)
        kim = host.state()["members"][1]
        assert kim["voice"] is True and kim["muted"] is True
        # Leaving voice clears the mute too: "muted" means nothing out of voice.
        await lobby_server.set_voice(guest, False, True)
        kim = host.state()["members"][1]
        assert kim["voice"] is False and kim["muted"] is False

    asyncio.run(go())


def test_signals_reach_only_the_named_member_stamped_with_the_real_sender():
    async def go():
        host, guest, third = FakeConn(), FakeConn(), FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        await lobby_server.join(guest, lobby.id, "kim")
        await lobby_server.join(third, lobby.id, "sam")
        kim_id = guest.state()["you"]
        rob_id = host.state()["you"]
        revision = lobby.revision

        await lobby_server.signal(
            host, kim_id, {"kind": "offer", "sdp": "v=0", "from": "forged"}
        )
        [frame] = guest.events("lobby_signal")
        # The sender comes from the socket, never from the frame.
        assert frame["from"] == rob_id
        assert frame["signal"]["sdp"] == "v=0"
        assert third.events("lobby_signal") == []
        # A handshake is not a state change; nobody gets a re-render for it.
        assert lobby.revision == revision

    asyncio.run(go())


def test_malformed_or_oversized_signals_are_dropped():
    async def go():
        host, guest = FakeConn(), FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        await lobby_server.join(guest, lobby.id, "kim")
        kim_id = guest.state()["you"]
        await lobby_server.signal(host, kim_id, {"kind": "exec", "cmd": "x"})
        await lobby_server.signal(host, kim_id, "not a dict")
        await lobby_server.signal(host, kim_id, {"kind": "offer", "sdp": "x" * 40_000})
        await lobby_server.signal(host, "nobody", {"kind": "ice"})
        assert guest.events("lobby_signal") == []

    asyncio.run(go())


def test_a_remote_member_signals_through_the_fabric():
    async def go():
        host = FakeConn()
        lobby = await lobby_server.create(host, "rob", "hd_assault")
        hub = FakeHub()
        session = FakeSession("nodeA")
        await fabric.handle_lobby(
            hub,
            session,
            env({"op": "join", "client": "c1", "lobby": lobby.id, "name": "kim"}),
        )
        rob_id = host.state()["you"]
        kim_id = host.state()["members"][1]["id"]

        await fabric.handle_lobby(
            hub,
            session,
            env({"op": "voice", "client": "c1", "on": True, "muted": False}),
        )
        assert host.state()["members"][1]["voice"] is True

        await fabric.handle_lobby(
            hub,
            session,
            env(
                {
                    "op": "signal",
                    "client": "c1",
                    "to": rob_id,
                    "signal": {"kind": "answer", "sdp": "v=0"},
                }
            ),
        )
        assert host.events("lobby_signal")[-1]["from"] == kim_id

        # And back the other way, addressed to the peer's browser.
        await lobby_server.signal(host, kim_id, {"kind": "ice", "candidate": {}})
        assert hub.messages("nodeA")[-1]["event"] == "lobby_signal"

    asyncio.run(go())
