"""Cross-node matches, invites, and the HorribleAssault agent tools.

The fabric is faked rather than stood up: two real `PeerHub`s over a real
transport would test the fabric, which has its own suite, not the bridge this
module is. What matters here is that a peer's player behaves exactly like a
browser's, that trust actually gates every entry point, and that a departing peer
does not leave a body standing in the match.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.hassault import agent_tools, channel, fabric
from backend.modules.hassault.match import MatchRoom, match_server
from backend.modules.hassault.physics import flat_world
from backend.modules.network.models import PeerEnvelope


class Spawn:
    def __init__(self, x: float, y: float, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = 0.0
        self.yaw = 0.0
        self.attr2 = team


class FakePeerInfo:
    def __init__(self, node_id: str, trusted: bool = True) -> None:
        self.node_id = node_id
        self.node_name = f"node-{node_id}"
        self.trusted = trusted
        self.capabilities = ["agent", "collab", "hassault"]


class FakeSession:
    def __init__(self, node_id: str, trusted: bool = True) -> None:
        self.info = FakePeerInfo(node_id, trusted)


class FakeHub:
    """Records what would have gone onto the wire."""

    def __init__(self, reachable: set[str] | None = None) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self.reachable = reachable

    async def send_to(
        self, node_id: str, msg_type: str, data: dict[str, Any], re: str | None = None
    ) -> None:
        if self.reachable is not None and node_id not in self.reachable:
            raise KeyError(node_id)
        self.sent.append((node_id, msg_type, data))

    def of_type(self, msg_type: str) -> list[dict[str, Any]]:
        return [d for _, t, d in self.sent if t == msg_type]


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m.get("event") == name]


def env(data: dict[str, Any]) -> PeerEnvelope:
    return PeerEnvelope(
        type="hassault_join", msg_id="m1", src="peer", ts=0.0, data=data
    )


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts with no rooms, no hosted guests, no invites.

    All three are process-global — the match server is a singleton by design —
    so without this the tests order-depend on each other.
    """
    yield
    match_server.rooms.clear()
    match_server.membership.clear()
    fabric._hosted.clear()
    fabric._remote.clear()
    fabric._invites.clear()


def make_room(room_id: str = "r1") -> MatchRoom:
    room = MatchRoom(
        room_id, "testmap", flat_world(32), [Spawn(8, 8), Spawn(20, 20, 1)]
    )
    match_server.rooms[room_id] = room
    return room


# ---------------------------------------------------------------------------
# Hosting a player who is on another node
# ---------------------------------------------------------------------------


def test_a_peer_joins_and_becomes_an_ordinary_player():
    async def go():
        room = make_room()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": "r1", "name": "rob"}),
        )
        assert len(room.players) == 1
        player = next(iter(room.players.values()))
        # The peer's claimed name is a *label*, tagged with the node it actually
        # arrived from — identity is the authenticated node id, so a trusted
        # friend's node can't send a nameplate that reads as a local account.
        assert player.name == "rob@nodeA"
        # The match server has no notion of "remote" — it just has a conn.
        assert isinstance(player.conn, fabric.PeerPlayerConn)

        welcome = [
            d for d in hub.of_type(fabric.HASSAULT_FRAME) if d.get("event") == "welcome"
        ]
        assert welcome and welcome[0]["client"] == "c1"
        assert welcome[0]["playerId"] == player.id

    asyncio.run(go())


def test_an_untrusted_peer_is_refused():
    """Friendship grants reachability. A node that merely knows a room id is not
    a friend, and must not be able to walk into a match."""

    async def go():
        room = make_room()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("stranger", trusted=False),
            env({"client": "c1", "room": "r1", "name": "nope"}),
        )
        assert not room.players
        errors = [
            d for d in hub.of_type(fabric.HASSAULT_FRAME) if d.get("event") == "error"
        ]
        assert errors and "trusted" in errors[0]["data"]["message"]

    asyncio.run(go())


def test_untrusted_input_and_invites_are_ignored():
    async def go():
        room = make_room()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": "r1", "name": "rob"}),
        )
        player = next(iter(room.players.values()))
        await fabric.handle_input(
            hub,
            FakeSession("nodeA", trusted=False),
            env({"client": "c1", "commands": [{"seq": 1, "forward": 1, "dt": 0.016}]}),
        )
        assert not player.queue

        await fabric.handle_invite(
            hub, FakeSession("stranger", trusted=False), env({"room": "x", "map": "m"})
        )
        assert not fabric.live_invites()

    asyncio.run(go())


def test_remote_input_goes_through_the_same_validation_as_a_browser():
    """A peer is another user's machine, not a trusted extension of ours — the
    axes have to be clamped on the way in exactly as a browser's are."""

    async def go():
        room = make_room()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": "r1", "name": "rob"}),
        )
        player = next(iter(room.players.values()))
        await fabric.handle_input(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "commands": [{"seq": 1, "forward": 99, "dt": 99}]}),
        )
        assert len(player.queue) == 1
        assert player.queue[0].forward == 1.0
        assert player.queue[0].dt == 0.25

    asyncio.run(go())


def test_a_remote_player_receives_snapshots_over_the_wire():
    async def go():
        room = make_room()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": "r1", "name": "rob"}),
        )
        hub.sent.clear()
        await match_server._broadcast(room)
        frames = hub.of_type(fabric.HASSAULT_FRAME)
        assert frames and frames[0]["event"] == "snapshot"
        # Tagged with the browser it belongs to: one machine can hold two tabs in
        # the same match, and without this both would go to whichever joined last.
        assert frames[0]["client"] == "c1"

    asyncio.run(go())


def test_two_tabs_on_one_peer_are_separate_players():
    async def go():
        room = make_room()
        hub = FakeHub()
        for client in ("c1", "c2"):
            await fabric.handle_join(
                hub,
                FakeSession("nodeA"),
                env({"client": client, "room": "r1", "name": client}),
            )
        assert len(room.players) == 2

    asyncio.run(go())


def test_rejoining_with_the_same_client_replaces_the_player():
    async def go():
        room = make_room()
        hub = FakeHub()
        for _ in range(3):
            await fabric.handle_join(
                hub,
                FakeSession("nodeA"),
                env({"client": "c1", "room": "r1", "name": "rob"}),
            )
        assert len(room.players) == 1

    asyncio.run(go())


def test_a_departed_peer_takes_its_players_with_it():
    """A remote player has no browser socket, so nothing else would ever notice
    they are gone — they would stand in the match forever."""

    async def go():
        room = make_room()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": "r1", "name": "rob"}),
        )
        await fabric.handle_join(
            hub,
            FakeSession("nodeB"),
            env({"client": "c9", "room": "r1", "name": "kim"}),
        )
        assert len(room.players) == 2

        await fabric.drop_peer("nodeA")
        assert len(room.players) == 1
        assert next(iter(room.players.values())).name == "kim@nodeB"
        assert fabric.hosted_count() == 1

    asyncio.run(go())


def test_joining_a_room_that_is_not_here_reports_an_error():
    async def go():
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": "nope", "name": "rob"}),
        )
        errors = [
            d for d in hub.of_type(fabric.HASSAULT_FRAME) if d.get("event") == "error"
        ]
        assert errors and "nope" in errors[0]["data"]["message"]

    asyncio.run(go())


def test_remote_respawn_moves_the_player():
    async def go():
        room = make_room()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": "r1", "name": "rob"}),
        )
        player = next(iter(room.players.values()))
        player.state.x = 99.0
        await fabric.handle_input(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "respawn": True, "commands": []}),
        )
        assert player.state.x != 99.0

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Guest side: relaying the host's frames to the right browser
# ---------------------------------------------------------------------------


def test_a_hosts_frame_reaches_the_bound_browser():
    async def go():
        conn = FakeConn()
        fabric.bind_remote(conn, "hostnode", "r1", "c1")  # type: ignore[arg-type]
        hub = FakeHub()
        await fabric.handle_frame(
            hub,
            FakeSession("hostnode"),
            env(
                {
                    "client": "c1",
                    "channel": "hassault",
                    "event": "snapshot",
                    "data": {"ack": 5},
                }
            ),
        )
        assert conn.events("snapshot") == [{"ack": 5}]

    asyncio.run(go())


def test_a_welcome_is_reshaped_so_the_browser_cannot_tell_the_difference():
    """Local and remote matches must produce the same browser events, or the
    client needs two code paths for one feature."""

    async def go():
        conn = FakeConn()
        fabric.bind_remote(conn, "hostnode", "r1", "c1")  # type: ignore[arg-type]
        await fabric.handle_frame(
            FakeHub(),
            FakeSession("hostnode"),
            env(
                {
                    "client": "c1",
                    "event": "welcome",
                    "room": "r1",
                    "map": "ac_desert",
                    "playerId": "p1",
                    "players": [],
                }
            ),
        )
        welcome = conn.events("welcome")
        assert welcome and welcome[0]["playerId"] == "p1"
        assert welcome[0]["map"] == "ac_desert"
        # Plus the one thing a local welcome cannot carry: whose node it is.
        assert welcome[0]["host"] == "hostnode"

    asyncio.run(go())


def test_a_frame_for_an_unknown_client_is_dropped():
    async def go():
        conn = FakeConn()
        fabric.bind_remote(conn, "hostnode", "r1", "c1")  # type: ignore[arg-type]
        await fabric.handle_frame(
            FakeHub(),
            FakeSession("hostnode"),
            env(
                {
                    "client": "someone-else",
                    "channel": "hassault",
                    "event": "snapshot",
                    "data": {},
                }
            ),
        )
        assert conn.sent == []

    asyncio.run(go())


def test_a_frame_from_the_wrong_node_is_dropped():
    """The binding names a host. A different node's frames for the same client id
    are not ours to render."""

    async def go():
        conn = FakeConn()
        fabric.bind_remote(conn, "hostnode", "r1", "c1")  # type: ignore[arg-type]
        await fabric.handle_frame(
            FakeHub(),
            FakeSession("othernode"),
            env(
                {"client": "c1", "channel": "hassault", "event": "snapshot", "data": {}}
            ),
        )
        assert conn.sent == []

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


def test_an_invite_is_stored_and_fanned_to_browsers():
    async def go():
        await fabric.handle_invite(
            FakeHub(),
            FakeSession("hostnode"),
            env({"room": "r7", "map": "ac_complex", "hostName": "Rob"}),
        )
        invites = fabric.live_invites()
        assert len(invites) == 1
        assert invites[0]["room"] == "r7"
        assert invites[0]["map"] == "ac_complex"
        # The node id is authenticated by the fabric; the name is only a label.
        assert invites[0]["host"] == "hostnode"

    asyncio.run(go())


def test_a_repeated_invite_refreshes_rather_than_stacking():
    async def go():
        for _ in range(3):
            await fabric.handle_invite(
                FakeHub(), FakeSession("hostnode"), env({"room": "r7", "map": "m"})
            )
        assert len(fabric.live_invites()) == 1

    asyncio.run(go())


def test_expired_invites_are_pruned_on_read():
    async def go():
        await fabric.handle_invite(
            FakeHub(), FakeSession("hostnode"), env({"room": "r7", "map": "m"})
        )
        fabric._invites["r7"]["ts"] -= fabric.INVITE_TTL + 1
        assert fabric.live_invites() == []

    asyncio.run(go())


def test_inviting_someone_who_is_not_a_friend_fails_clearly():
    async def go():
        make_room()
        result = await channel.invite_friend("nobody-by-that-name", "r1")
        assert "error" in result
        assert "nobody-by-that-name" in result["error"]

    asyncio.run(go())


def test_inviting_to_a_room_that_does_not_exist_fails():
    async def go():
        result = await channel.invite_friend("anyone", "no-such-room")
        assert "error" in result and "no-such-room" in result["error"]

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------


def test_host_tool_reports_a_missing_map_rather_than_raising():
    async def go():
        result = await agent_tools.host_match({"map": "definitely_not_a_map"})
        assert "error" in result

    asyncio.run(go())


def test_host_tool_needs_a_map():
    assert "error" in asyncio.run(agent_tools.host_match({}))


def test_status_tool_disambiguates_when_several_matches_run():
    async def go():
        make_room("a")
        make_room("b")
        result = await agent_tools.match_status({})
        assert "error" in result and "which room" in result["error"]

    asyncio.run(go())


def test_status_tool_defaults_to_the_only_match():
    async def go():
        room = make_room()
        room.add("alice", None)
        result = await agent_tools.match_status({})
        assert result["room"] == room.id
        assert [p["name"] for p in result["players"]] == ["alice"]
        assert result["players"][0]["team"] in ("CLA", "RVSF")
        assert result["players"][0]["remote"] is False

    asyncio.run(go())


def test_status_tool_flags_players_from_another_node():
    async def go():
        room = make_room()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": room.id, "name": "rob"}),
        )
        result = await agent_tools.match_status({"room": room.id})
        assert result["players"][0]["remote"] is True

    asyncio.run(go())


def test_surroundings_measures_the_walls():
    async def go():
        # A room open from 2..29 with a solid border, so the distances are known.
        room = make_room()
        player = room.add("alice", None)
        player.state.x = 16.0
        player.state.y = 16.0
        player.state.z = 0.0
        result = await agent_tools.describe_surroundings(
            {"room": room.id, "player": "alice"}
        )
        assert result["player"] == "alice"
        # 16 -> the wall at x=30 is 14 cubes away; the probe caps at 24, so this
        # is a real measurement rather than the cap.
        assert result["clear_distance"]["east"] == pytest.approx(13, abs=1)
        assert result["clear_distance"]["west"] == pytest.approx(13, abs=1)
        assert result["body_width"] == pytest.approx(2.2)

    asyncio.run(go())


def test_surroundings_reports_others_by_distance_and_bearing():
    async def go():
        room = make_room()
        alice = room.add("alice", None)
        bob = room.add("bob", None)
        alice.state.x, alice.state.y = 10.0, 10.0
        bob.state.x, bob.state.y = 20.0, 10.0
        result = await agent_tools.describe_surroundings(
            {"room": room.id, "player": "alice"}
        )
        assert result["others"][0]["name"] == "bob"
        assert result["others"][0]["distance"] == pytest.approx(10.0)
        # Compass words, because the agent is talking to a human.
        assert result["others"][0]["bearing"] == "east"

    asyncio.run(go())


def test_surroundings_needs_a_match():
    assert "error" in asyncio.run(agent_tools.describe_surroundings({}))


def test_bearing_covers_the_compass():
    assert agent_tools._bearing(0, 0, 1, 0) == "east"
    assert agent_tools._bearing(0, 0, 0, 1) == "north"
    assert agent_tools._bearing(0, 0, -1, 0) == "west"
    assert agent_tools._bearing(0, 0, 0, -1) == "south"
    assert agent_tools._bearing(0, 0, 1, 1) == "north-east"


def test_register_wires_every_handler_onto_a_real_hub():
    """A real `PeerHub`, not a fake: this is the wiring that decides whether an
    invite from a friend is dispatched or silently dropped, and a fake hub would
    happily accept handlers the real one never routes."""
    from backend.modules.network.hub import PeerHub

    hub = PeerHub()
    fabric.register(hub)
    for msg_type in (
        fabric.HASSAULT_INVITE,
        fabric.HASSAULT_JOIN,
        fabric.HASSAULT_INPUT,
        fabric.HASSAULT_LEAVE,
        fabric.HASSAULT_FRAME,
    ):
        assert msg_type in hub._handlers, f"{msg_type} was not registered"


def test_the_node_advertises_the_hassault_capability():
    """Advertised during the handshake so a friend's UI can grey out an invite to
    a node that could not accept it."""
    from backend.modules.network.hub import PeerHub

    assert fabric.CAPABILITY in PeerHub().capabilities()


# ---------------------------------------------------------------------------
# hosted_rooms: locating a remote friend's players
# ---------------------------------------------------------------------------


def test_hosted_rooms_maps_a_node_to_the_room_its_players_are_in():
    async def go():
        make_room()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": "r1", "name": "rob"}),
        )
        assert fabric.hosted_rooms() == {"nodeA": "r1"}

    asyncio.run(go())


def test_hosted_rooms_is_empty_with_no_remote_players():
    assert fabric.hosted_rooms() == {}


def test_hosted_rooms_takes_the_first_room_for_two_tabs_on_one_node():
    """Two browsers on one machine could be in two different rooms; the server
    browser is answering "where is this friend", not enumerating their tabs, so
    one room per node is all it promises."""

    async def go():
        room_a = make_room("ra")
        room_b = make_room("rb")
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c1", "room": room_a.id, "name": "rob"}),
        )
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            env({"client": "c2", "room": room_b.id, "name": "rob"}),
        )
        assert fabric.hosted_rooms()["nodeA"] in (room_a.id, room_b.id)

    asyncio.run(go())


# ---------------------------------------------------------------------------
# The server browser
# ---------------------------------------------------------------------------


def test_handle_browse_answers_with_the_local_listing():
    async def go():
        make_room()
        hub = FakeHub()
        await fabric.handle_browse(hub, FakeSession("friend"), env({}))
        replies = hub.of_type(fabric.HASSAULT_BROWSE)
        assert len(replies) == 1
        assert replies[0]["matches"][0]["id"] == "r1"
        assert "hostName" in replies[0]

    asyncio.run(go())


def test_handle_browse_refuses_an_untrusted_peer():
    async def go():
        make_room()
        hub = FakeHub()
        await fabric.handle_browse(hub, FakeSession("stranger", trusted=False), env({}))
        assert hub.of_type(fabric.HASSAULT_BROWSE) == []

    asyncio.run(go())


class FakePeer:
    def __init__(
        self, node_id: str, node_name: str, trusted: bool, capable: bool
    ) -> None:
        self.node_id = node_id
        self.node_name = node_name
        self.trusted = trusted
        self.capabilities = ["hassault"] if capable else []


class FakePeerHub:
    """Stands in for `network.hub.peer_hub` in `browse_peers`.

    `answers` maps node id to either a reply payload or `None` for "never
    answers" — the case a real request would time out on.
    """

    def __init__(
        self, peers: list[FakePeer], answers: dict[str, dict[str, Any] | None]
    ) -> None:
        self._peers = peers
        self._answers = answers

    def list_peers(self) -> list[FakePeer]:
        return self._peers

    async def request(
        self, node_id: str, msg_type: str, data: dict[str, Any], timeout: float = 2.0
    ) -> Any:
        reply = self._answers.get(node_id)
        if reply is None:
            raise TimeoutError(node_id)

        class _Env:
            def __init__(self, data: dict[str, Any]) -> None:
                self.data = data

        return _Env(reply)


def test_browse_peers_asks_only_trusted_capable_friends(monkeypatch):
    async def go():
        peers = [
            FakePeer("trusted-capable", "Kim", trusted=True, capable=True),
            FakePeer("trusted-not-capable", "Sam", trusted=True, capable=False),
            FakePeer("untrusted-capable", "Stranger", trusted=False, capable=True),
        ]
        fake_hub = FakePeerHub(
            peers,
            {
                "trusted-capable": {
                    "matches": [
                        {
                            "id": "m1",
                            "map": "ac_desert",
                            "players": 1,
                            "bots": 0,
                            "maxPlayers": 8,
                            "createdAt": 0.0,
                        }
                    ],
                    "hostName": "Kim's Box",
                }
            },
        )
        import backend.modules.network.hub as hub_module

        monkeypatch.setattr(hub_module, "peer_hub", fake_hub)

        rows, asked, answered = await fabric.browse_peers()
        assert asked == 1  # only the trusted + capable peer was worth asking
        assert answered == 1
        assert len(rows) == 1
        assert rows[0]["host"] == "trusted-capable"
        assert rows[0]["hostName"] == "Kim"  # the roster's name wins over the claim
        assert rows[0]["map"] == "ac_desert"

    asyncio.run(go())


def test_browse_peers_drops_a_peer_that_never_answers(monkeypatch):
    async def go():
        peers = [FakePeer("slow", "Slow Friend", trusted=True, capable=True)]
        fake_hub = FakePeerHub(peers, {"slow": None})
        import backend.modules.network.hub as hub_module

        monkeypatch.setattr(hub_module, "peer_hub", fake_hub)

        rows, asked, answered = await fabric.browse_peers()
        assert asked == 1
        assert answered == 0
        assert rows == []

    asyncio.run(go())


def test_browse_peers_with_no_capable_friends_asks_nobody(monkeypatch):
    async def go():
        fake_hub = FakePeerHub([], {})
        import backend.modules.network.hub as hub_module

        monkeypatch.setattr(hub_module, "peer_hub", fake_hub)

        rows, asked, answered = await fabric.browse_peers()
        assert (rows, asked, answered) == ([], 0, 0)

    asyncio.run(go())


def test_the_browse_handler_is_registered():
    from backend.modules.network.hub import PeerHub

    hub = PeerHub()
    fabric.register(hub)
    assert fabric.HASSAULT_BROWSE in hub._handlers


def test_tools_are_registered_under_the_module_prefix():
    """The orchestrator groups tools by name prefix, so the prefix has to match
    the module id — `AgentTool.group` does not name the group.

    Asserted as a **set of names**, not a count. This assertion used to read
    `len(names) == 8` and went red when the developer console added two tools; a
    bare count tells you that something moved but not what, so the failure looks
    identical whether a tool was added, renamed, or silently lost its prefix — and
    losing the prefix is the actual bug this test exists to catch, since a tool
    named outside `hassault.` lands in a group of its own and quietly stops being
    loadable with the rest.
    """
    from backend.sdk.registry import registry

    agent_tools.register_hassault_tools()
    names = {n for n in registry.agent_tools if n.startswith("hassault.")}
    assert names == {
        "hassault.list_maps",
        "hassault.list_matches",
        "hassault.host",
        "hassault.invite",
        "hassault.status",
        "hassault.surroundings",
        "hassault.add_bot",
        "hassault.remove_bot",
        # The developer console (see docs/modules/hassault.mdx).
        "hassault.console_exec",
        "hassault.run_macro",
    }
    for name in names:
        assert registry.agent_tools[name].group == "hassault"
