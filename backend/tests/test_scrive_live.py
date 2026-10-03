"""Live co-editing's relay, between two simulated machines.

Each machine is its own `LiveRelay`; the fabric between them is a direct call (the
real hub's signing and transport are the network module's, tested there). What must
hold: an invitation reaches only the invited person's machine; a guest's edits go to
the host and the host fans them out to its browsers and other guests, never back to
the sender; a message for a room from a node that is not in it is dropped, as is
anything from an untrusted peer; ending the session reaches the guests; a host that
reloads reseeds and tells the guests to reset, while a second pane on the host joins
the first; and an oversized frame is refused rather than severing the link.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from backend.modules.scrive import live


class Conn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m["event"] == name]


class Machine:
    """A node: its relay, wired to the others through `fabric`."""

    def __init__(self, node: str, fabric: dict[str, Machine]) -> None:
        self.node = node
        self.relay = live.LiveRelay()
        self.fabric = fabric
        self.invited: list[dict[str, Any]] = []
        fabric[node] = self

        async def to_peers(
            room: live.Room, nodes: set[str], data: dict[str, Any]
        ) -> None:
            for target in sorted(nodes):
                if target in self.fabric:
                    await self.fabric[target].relay.from_peer(
                        self.node, {"key": room.key, **data}
                    )

        self.relay._to_peers = to_peers  # type: ignore[method-assign]

    async def send(self, conn: Conn, event: str, **data: Any) -> None:
        await self.relay.handle(conn, {"event": event, "data": data})


@pytest.fixture
def net(monkeypatch):
    from backend.modules.social import roster
    from backend.modules.social import store as social_store
    from backend.modules import ws

    fabric: dict[str, Machine] = {}
    host = Machine("node-host", fabric)
    guest = Machine("node-guest", fabric)
    other = Machine("node-other", fabric)
    people = {"ada": ["node-guest"], "grace": ["node-other"]}
    monkeypatch.setattr(
        roster, "reachable_nodes", lambda person: people.get(person, [])
    )
    monkeypatch.setattr(
        roster, "self_profile", lambda: SimpleNamespace(display_name="Hosty")
    )
    monkeypatch.setattr(
        social_store,
        "person_for_node",
        lambda node: next((p for p, nodes in people.items() if node in nodes), None),
    )
    monkeypatch.setattr(
        social_store, "get_friend_row", lambda person: {"display_name": person.title()}
    )
    monkeypatch.setattr(
        social_store,
        "list_devices",
        lambda person: [{"node_id": n} for n in people.get(person, [])],
    )
    broadcasts: list[tuple[str, str, dict[str, Any]]] = []

    async def broadcast(channel: str, event: str, data: dict[str, Any]) -> None:
        broadcasts.append((channel, event, data))

    monkeypatch.setattr(ws, "broadcast_event", broadcast)
    return SimpleNamespace(host=host, guest=guest, other=other, broadcasts=broadcasts)


def run(coro):
    return asyncio.run(coro)


async def hosted(net, conn: Conn) -> str:
    await net.host.send(conn, "host", site="blog", path="posts/a.md", title="Priors")
    return conn.events("hosted")[-1]["key"]


def test_an_invitation_reaches_only_the_invited_machine(net) -> None:
    async def go():
        me = Conn()
        key = await hosted(net, me)
        await net.host.send(me, "invite", key=key, personId="ada")
        return key, me

    key, me = run(go())
    assert key in net.guest.relay.rooms and key not in net.other.relay.rooms
    room = net.guest.relay.rooms[key]
    assert room.role == "guest" and room.host_node == "node-host"
    assert room.title == "Priors" and room.host_name == "Hosty"
    assert ("scrive", "live.invited") == net.broadcasts[-1][:2]
    assert me.events("people")[-1]["people"] == [{"personId": "ada", "name": "Ada"}]


def test_edits_go_through_the_host_and_never_back_to_the_sender(net) -> None:
    async def go():
        me, also_me = Conn(), Conn()
        key = await hosted(net, me)
        await net.host.send(also_me, "join", key=key)
        await net.host.send(me, "invite", key=key, personId="ada")
        await net.host.send(me, "invite", key=key, personId="grace")
        ada, grace = Conn(), Conn()
        await net.guest.send(ada, "join", key=key)
        await net.other.send(grace, "join", key=key)
        await net.guest.send(ada, "msg", key=key, data="QURB")  # Ada types
        return me, also_me, ada, grace

    me, also_me, ada, grace = run(go())
    assert [m["data"] for m in me.events("msg")] == ["QURB"]
    assert [m["data"] for m in also_me.events("msg")] == ["QURB"]
    assert [m["data"] for m in grace.events("msg")] == ["QURB"]
    assert ada.events("msg") == []  # not echoed to the one who typed


def test_a_node_outside_the_room_is_not_heard(net) -> None:
    async def go():
        me = Conn()
        key = await hosted(net, me)
        await net.host.send(me, "invite", key=key, personId="ada")
        # Grace's machine was never invited, but knows the key.
        await net.host.relay.from_peer(
            "node-other", {"key": key, "op": "msg", "data": "eA=="}
        )
        return me

    me = run(go())
    assert me.events("msg") == []


def test_an_untrusted_peer_is_dropped_before_the_relay(net, monkeypatch) -> None:
    seen: list[str] = []

    async def from_peer(src: str, data: dict[str, Any]) -> None:
        seen.append(src)

    monkeypatch.setattr(live.relay, "from_peer", from_peer)
    env = SimpleNamespace(src="node-x", data={"key": "k", "op": "msg", "data": "eA=="})
    run(
        live.handle_peer_live(
            None, SimpleNamespace(info=SimpleNamespace(trusted=False)), env
        )
    )
    assert seen == []
    run(
        live.handle_peer_live(
            None, SimpleNamespace(info=SimpleNamespace(trusted=True)), env
        )
    )
    assert seen == ["node-x"]


def test_ending_reaches_the_guests(net) -> None:
    async def go():
        me, ada = Conn(), Conn()
        key = await hosted(net, me)
        await net.host.send(me, "invite", key=key, personId="ada")
        await net.guest.send(ada, "join", key=key)
        await net.host.send(me, "end", key=key)
        return key, ada

    key, ada = run(go())
    assert ada.events("ended") == [{"key": key}]
    assert key not in net.guest.relay.rooms and key not in net.host.relay.rooms
    assert net.broadcasts[-1][1] == "live.ended"


def test_a_second_pane_joins_but_a_reload_reseeds(net) -> None:
    async def go():
        first, second = Conn(), Conn()
        key = await hosted(net, first)
        await net.host.send(first, "invite", key=key, personId="ada")
        ada = Conn()
        await net.guest.send(ada, "join", key=key)
        # Another pane on the host while the first is open: it joins.
        await net.host.send(
            second, "host", site="blog", path="posts/a.md", title="Priors"
        )
        joined = second.events("hosted")[-1]
        # Both host panes go (a reload), then one comes back within the grace period.
        await net.host.send(first, "leave", key=key)
        net.host.relay.rooms[key].members.clear()
        back = Conn()
        await net.host.send(
            back, "host", site="blog", path="posts/a.md", title="Priors"
        )
        return key, joined, back.events("hosted")[-1], ada

    key, joined, reseeded, ada = run(go())
    assert joined["key"] == key and joined["existing"] is True
    assert reseeded["key"] == key and reseeded["existing"] is False
    assert ada.events("reset") == [{"key": key}]


def test_the_last_host_pane_leaving_ends_the_session(net) -> None:
    async def go():
        me = Conn()
        key = await hosted(net, me)
        await net.host.send(me, "leave", key=key)
        return key

    key = run(go())
    assert key not in net.host.relay.rooms


def test_an_oversized_frame_is_refused(net) -> None:
    async def go():
        me, other = Conn(), Conn()
        key = await hosted(net, me)
        await net.host.send(other, "join", key=key)
        await net.host.send(me, "msg", key=key, data="x" * (live.MAX_MESSAGE_CHARS + 1))
        return me, other

    me, other = run(go())
    assert other.events("msg") == []
    assert "too large" in me.events("error")[-1]["message"]


def test_a_room_that_does_not_exist_says_so(net) -> None:
    conn = Conn()
    run(net.guest.send(conn, "join", key="nope"))
    assert "ended" in conn.events("error")[-1]["message"]
