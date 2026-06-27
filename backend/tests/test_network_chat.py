"""Peer chat tests: the ChatManager's history/open flow and the inbound peer-message
path. No sockets — fake member connections capture sends."""

import asyncio

from backend.modules.network.chat import ChatManager
from backend.modules.network.models import PeerEnvelope


class FakeConn:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    def events(self):
        return [(s["event"], s["data"]) for s in self.sent]


def test_open_returns_empty_history():
    mgr = ChatManager()
    conn = FakeConn()
    asyncio.run(mgr.handle(conn, {"event": "open", "data": {"nodeId": "peerX"}}))
    ev, data = conn.events()[-1]
    assert ev == "history"
    assert data == {"nodeId": "peerX", "messages": []}


def test_peer_message_recorded_and_fanned_out():
    mgr = ChatManager()
    conn = FakeConn()

    async def go():
        # A subscribed browser tab...
        await mgr.handle(conn, {"event": "open", "data": {"nodeId": "peerX"}})
        # ...receives an inbound message relayed from the peer wire.
        env = PeerEnvelope(
            type="peer_chat",
            src="peerX",
            data={"text": "hello from peer", "from_name": "Alice"},
        )
        await mgr.apply_peer_chat(env)

    asyncio.run(go())
    assert any(
        ev == "message"
        and d["text"] == "hello from peer"
        and d["from"] == "Alice"
        and d["direction"] == "in"
        and d["nodeId"] == "peerX"
        for ev, d in conn.events()
    )


def test_history_replays_prior_messages():
    mgr = ChatManager()
    first, second = FakeConn(), FakeConn()

    async def go():
        await mgr.handle(first, {"event": "open", "data": {"nodeId": "peerX"}})
        env = PeerEnvelope(
            type="peer_chat", src="peerX", data={"text": "earlier", "from_name": "A"}
        )
        await mgr.apply_peer_chat(env)
        # A freshly opened panel gets the backlog.
        await mgr.handle(second, {"event": "open", "data": {"nodeId": "peerX"}})

    asyncio.run(go())
    ev, data = second.events()[-1]
    assert ev == "history"
    assert [m["text"] for m in data["messages"]] == ["earlier"]


def test_dropped_conn_stops_receiving():
    mgr = ChatManager()
    conn = FakeConn()

    async def go():
        await mgr.handle(conn, {"event": "open", "data": {"nodeId": "peerX"}})
        mgr.drop(conn)
        env = PeerEnvelope(
            type="peer_chat", src="peerX", data={"text": "after drop", "from_name": "A"}
        )
        await mgr.apply_peer_chat(env)

    asyncio.run(go())
    assert not any(ev == "message" for ev, _ in conn.events())
