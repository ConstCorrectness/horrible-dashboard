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


# ---- who an op actually reaches ------------------------------------------------------
#
# The leak this fixes: `_forward_to_peers` iterated `peer_hub.peers` — every node
# with a live session — so the full text of every shared pane was delivered to
# everyone you were connected to, whether or not they had any part in it.


class FakeHub:
    """Just enough of the hub: a connection table and a record of what was sent."""

    def __init__(self, *node_ids):
        self.peers = dict.fromkeys(node_ids, object())
        self.sent = []

    async def send_to(self, node_id, type_, data):
        self.sent.append((node_id, data))


def _hub(monkeypatch, *node_ids):
    hub = FakeHub(*node_ids)
    monkeypatch.setattr("backend.modules.network.hub.peer_hub", hub)
    return hub


def test_an_op_reaches_nobody_until_the_pane_is_shared(monkeypatch):
    hub = _hub(monkeypatch, "peerA", "peerB")
    mgr = CollabManager()
    conn = FakeConn()

    async def go():
        await mgr.handle(conn, {"event": "join", "data": {"paneKey": "k"}})
        await mgr.handle(
            conn,
            {"event": "op", "data": {"paneKey": "k", "baseRev": 0, "text": "private"}},
        )

    asyncio.run(go())
    assert hub.sent == []


def test_a_third_peer_receives_nothing(monkeypatch):
    """Two peers in the room, one connected bystander — the plan's own check."""
    hub = _hub(monkeypatch, "peerA", "peerB", "bystander")
    mgr = CollabManager()
    conn = FakeConn()

    async def go():
        await mgr.handle(conn, {"event": "join", "data": {"paneKey": "k"}})
        # peerA is in because they sent us an op; peerB because we shared with them.
        await mgr.apply_peer_op(
            PeerEnvelope(
                type="collab_op",
                src="peerA",
                data={"paneKey": "k", "rev": 1, "text": "hi"},
            )
        )
        mgr.rooms["k"].peers.add("peerB")
        await mgr.handle(
            conn,
            {"event": "op", "data": {"paneKey": "k", "baseRev": 1, "text": "ours"}},
        )

    asyncio.run(go())
    assert {node for node, _ in hub.sent} == {"peerA", "peerB"}
    assert all(data["text"] == "ours" for _, data in hub.sent)


def test_a_peers_op_puts_them_in_the_room(monkeypatch):
    """How the far side of a share learns to route our edits back."""
    _hub(monkeypatch)
    mgr = CollabManager()

    async def go():
        await mgr.apply_peer_op(
            PeerEnvelope(
                type="collab_op",
                src="peerA",
                data={"paneKey": "k", "rev": 2, "text": "x"},
            )
        )

    asyncio.run(go())
    assert mgr.rooms["k"].peers == {"peerA"}


def test_sharing_pushes_the_current_state_to_their_machines(monkeypatch):
    from backend.modules.social import store as social_store

    hub = _hub(monkeypatch, "laptop", "desktop", "stranger")
    social_store.init_social_db()
    social_store.upsert_friend("p_ann", display_name="Ann", status="accepted")
    social_store.upsert_device("laptop", "p_ann", node_public_key="", label="laptop")
    monkeypatch.setattr(
        "backend.modules.social.roster.reachable_nodes", lambda pid: ["laptop"]
    )
    mgr = CollabManager()
    conn = FakeConn()

    async def go():
        await mgr.handle(conn, {"event": "join", "data": {"paneKey": "k"}})
        await mgr.handle(
            conn,
            {"event": "op", "data": {"paneKey": "k", "baseRev": 0, "text": "notes"}},
        )
        await mgr.handle(
            conn, {"event": "share", "data": {"paneKey": "k", "personId": "p_ann"}}
        )

    asyncio.run(go())
    # They open on what you are looking at, not on an empty pane.
    assert hub.sent == [("laptop", {"paneKey": "k", "rev": 1, "text": "notes"})]
    shared = [d for ev, d in conn.events() if ev == "shared"]
    assert shared[-1]["people"] == [{"personId": "p_ann", "name": "Ann"}]


def test_sharing_with_someone_offline_says_so(monkeypatch):
    _hub(monkeypatch)
    monkeypatch.setattr("backend.modules.social.roster.reachable_nodes", lambda pid: [])
    mgr = CollabManager()
    conn = FakeConn()

    async def go():
        await mgr.handle(conn, {"event": "join", "data": {"paneKey": "k"}})
        await mgr.handle(
            conn, {"event": "share", "data": {"paneKey": "k", "personId": "p_ann"}}
        )

    asyncio.run(go())
    ev, data = conn.events()[-1]
    assert ev == "error" and "online" in data["message"]
    assert mgr.rooms["k"].peers == set()


def test_unshare_removes_every_machine_of_theirs(monkeypatch):
    from backend.modules.social import store as social_store

    hub = _hub(monkeypatch, "laptop", "desktop")
    social_store.init_social_db()
    social_store.upsert_friend("p_ann", display_name="Ann", status="accepted")
    for node in ("laptop", "desktop"):
        social_store.upsert_device(node, "p_ann", node_public_key="", label=node)
    mgr = CollabManager()
    conn = FakeConn()

    async def go():
        await mgr.handle(conn, {"event": "join", "data": {"paneKey": "k"}})
        mgr.rooms["k"].peers.update({"laptop", "desktop"})
        await mgr.handle(
            conn, {"event": "unshare", "data": {"paneKey": "k", "personId": "p_ann"}}
        )
        hub.sent.clear()
        await mgr.handle(
            conn,
            {"event": "op", "data": {"paneKey": "k", "baseRev": 0, "text": "after"}},
        )

    asyncio.run(go())
    assert mgr.rooms["k"].peers == set()
    assert hub.sent == []


def test_an_untrusted_peers_op_is_ignored():
    """Knowing a pane key is not permission — an untrusted peer could otherwise
    both read the pane (we would start forwarding to them) and rewrite it."""
    from backend.modules.network.collab import handle_peer_collab_op

    class Info:
        trusted = False

    class Session:
        info = Info()

    env = PeerEnvelope(
        type="collab_op",
        src="rando",
        data={"paneKey": "k", "rev": 9, "text": "mine now"},
    )
    asyncio.run(handle_peer_collab_op(None, Session(), env))
    from backend.modules.network.collab import collab_manager

    assert "k" not in collab_manager.rooms
