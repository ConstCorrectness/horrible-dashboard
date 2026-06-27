"""Collaborative shared-pane tests: the CollabManager's rev-checked sync and the
inbound peer-op path. No sockets — fake member connections capture sends."""

import asyncio

from backend.modules.network.collab import CollabManager
from backend.modules.network.models import PeerEnvelope


class FakeConn:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    def events(self):
        return [(s["event"], s["data"]) for s in self.sent]


def test_join_returns_state():
    mgr = CollabManager()
    conn = FakeConn()
    asyncio.run(mgr.handle(conn, {"event": "join", "data": {"paneKey": "k"}}))
    ev, data = conn.events()[-1]
    assert ev == "state"
    assert data == {"paneKey": "k", "rev": 0, "text": "", "members": 1}


def test_op_broadcasts_and_increments_rev():
    mgr = CollabManager()
    a, b = FakeConn(), FakeConn()

    async def go():
        await mgr.handle(a, {"event": "join", "data": {"paneKey": "k"}})
        await mgr.handle(b, {"event": "join", "data": {"paneKey": "k"}})
        await mgr.handle(
            a, {"event": "op", "data": {"paneKey": "k", "baseRev": 0, "text": "hello"}}
        )

    asyncio.run(go())
    # Both members (incl. sender) see the accepted op at rev 1.
    assert any(
        ev == "op" and d["rev"] == 1 and d["text"] == "hello" for ev, d in b.events()
    )
    assert any(ev == "op" and d["rev"] == 1 for ev, d in a.events())


def test_stale_op_is_rejected_with_authoritative_state():
    mgr = CollabManager()
    a = FakeConn()

    async def go():
        await mgr.handle(a, {"event": "join", "data": {"paneKey": "k"}})
        await mgr.handle(
            a, {"event": "op", "data": {"paneKey": "k", "baseRev": 0, "text": "first"}}
        )
        # Reuse the now-stale baseRev 0 → rejected with the current authoritative text.
        await mgr.handle(
            a, {"event": "op", "data": {"paneKey": "k", "baseRev": 0, "text": "stale"}}
        )

    asyncio.run(go())
    ev, data = a.events()[-1]
    assert ev == "rejected"
    assert data["rev"] == 1
    assert data["text"] == "first"


def test_peer_op_applied_and_broadcast():
    mgr = CollabManager()
    a = FakeConn()

    async def go():
        await mgr.handle(a, {"event": "join", "data": {"paneKey": "k"}})
        env = PeerEnvelope(
            type="collab_op",
            src="peerX",
            data={"paneKey": "k", "rev": 5, "text": "from peer"},
        )
        await mgr.apply_peer_op(env)

    asyncio.run(go())
    assert any(
        ev == "op"
        and d["text"] == "from peer"
        and d["rev"] == 5
        and d["from"] == "peerX"
        for ev, d in a.events()
    )
