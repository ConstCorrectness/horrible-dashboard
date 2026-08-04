"""Peer chat: the person-keyed conversation store and the inbound peer path.

No sockets — fake member connections capture sends.

What these pin, all of which used to be untrue:

- A conversation is keyed by **person**, so a friend's laptop and desktop are one
  thread rather than two.
- History is **persisted**, so it survives the process.
- An inbound message nobody is looking at raises a **notification**, and one the
  reader is looking at does not.
- A message from a node with no person (a stranger, or a peer paired before the
  social layer existed) is still delivered, just unfiled — dropping it would be
  worse than not being able to name who it came from.
"""

import asyncio

from backend.modules.network.chat import ChatManager
from backend.modules.network.models import PeerEnvelope
from backend.modules.social import messages as message_store
from backend.modules.social import store as social_store


class FakeConn:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    def events(self):
        return [(s["event"], s["data"]) for s in self.sent]


def _known_peer(node_id="peerX", person_id="p_alice", name="Alice"):
    """A device row, so `person_for_node` can name whose machine this is."""
    social_store.init_social_db()
    social_store.upsert_friend(person_id, display_name=name, status="accepted")
    social_store.upsert_device(node_id, person_id, node_public_key="", label="laptop")
    return person_id


def _inbound(node_id="peerX", text="hello from peer", name="Alice"):
    return PeerEnvelope(
        type="peer_chat", src=node_id, data={"text": text, "from_name": name}
    )


def test_open_returns_empty_history():
    mgr = ChatManager()
    conn = FakeConn()
    asyncio.run(mgr.handle(conn, {"event": "open", "data": {"personId": "p_alice"}}))
    events = dict(conn.events())
    assert events["history"] == {"personId": "p_alice", "messages": []}


def test_peer_message_is_filed_under_the_person_and_fanned_out(monkeypatch):
    mgr = ChatManager()
    conn = FakeConn()
    person = _known_peer()
    sent = []
    monkeypatch.setattr(
        "backend.modules.notifications.service.notify",
        lambda *a, **k: _record(sent, a, k),
    )

    async def go():
        await mgr.handle(conn, {"event": "open", "data": {"personId": person}})
        await mgr.apply_peer_chat(_inbound())

    asyncio.run(go())
    assert any(
        ev == "message"
        and d["text"] == "hello from peer"
        and d["direction"] == "in"
        and d["personId"] == person
        # The node is recorded as the route it took, never as the identity.
        and d["nodeId"] == "peerX"
        for ev, d in conn.events()
    )
    assert [m["text"] for m in message_store.conversation(person)] == [
        "hello from peer"
    ]


async def _record(sink, args, kwargs):
    sink.append((args, kwargs))
    return True


def test_history_survives_a_new_manager():
    """The old store was an in-memory deque, so a restart erased everything."""
    person = _known_peer()

    async def go():
        first = ChatManager()
        await first.apply_peer_chat(_inbound(text="earlier"))
        # A brand-new manager stands in for a restarted process.
        second = ChatManager()
        conn = FakeConn()
        await second.handle(conn, {"event": "open", "data": {"personId": person}})
        return conn

    conn = asyncio.run(go())
    events = dict(conn.events())
    assert [m["text"] for m in events["history"]["messages"]] == ["earlier"]


def test_two_machines_are_one_conversation():
    person = _known_peer(node_id="laptop")
    social_store.upsert_device("desktop", person, node_public_key="", label="desktop")

    async def go():
        mgr = ChatManager()
        await mgr.apply_peer_chat(_inbound(node_id="laptop", text="from the laptop"))
        await mgr.apply_peer_chat(_inbound(node_id="desktop", text="from the desktop"))

    asyncio.run(go())
    assert [m["text"] for m in message_store.conversation(person)] == [
        "from the laptop",
        "from the desktop",
    ]


def test_unread_counts_and_marking_read():
    _known_peer()

    async def go():
        mgr = ChatManager()
        conn = FakeConn()
        # Subscribed to badges, but not looking at this conversation.
        await mgr.handle(conn, {"event": "unread", "data": {}})
        await mgr.apply_peer_chat(_inbound(text="one"))
        await mgr.apply_peer_chat(_inbound(text="two"))
        return mgr, conn

    mgr, conn = asyncio.run(go())
    assert message_store.unread_counts() == {"p_alice": 2}
    counts = [d["counts"] for ev, d in conn.events() if ev == "unread"]
    assert counts[-1] == {"p_alice": 2}

    asyncio.run(mgr.handle(conn, {"event": "read", "data": {"personId": "p_alice"}}))
    assert message_store.unread_counts() == {}


def test_a_message_you_are_reading_is_not_unread_and_raises_no_notification(
    monkeypatch,
):
    person = _known_peer()
    fired = []
    monkeypatch.setattr(
        "backend.modules.notifications.service.notify",
        lambda *a, **k: _record(fired, a, k),
    )

    async def go():
        mgr = ChatManager()
        conn = FakeConn()
        await mgr.handle(conn, {"event": "open", "data": {"personId": person}})
        await mgr.apply_peer_chat(_inbound(text="while you watch"))

    asyncio.run(go())
    assert message_store.unread_counts() == {}
    assert fired == []


def test_a_message_nobody_is_reading_raises_a_notification(monkeypatch):
    person = _known_peer()
    fired = []
    monkeypatch.setattr(
        "backend.modules.notifications.service.notify",
        lambda *a, **k: _record(fired, a, k),
    )

    async def go():
        mgr = ChatManager()
        await mgr.apply_peer_chat(_inbound(text="while you were out"))

    asyncio.run(go())
    assert len(fired) == 1
    args, kwargs = fired[0]
    assert args[0] == "message"
    # `person_id` is what a per-person mute matches on — without it, "mute Andrew"
    # could not apply to a message from Andrew.
    assert kwargs["person_id"] == person


def test_a_stranger_is_delivered_unfiled():
    """No device row ⇒ no person to file it under. Delivered anyway."""

    async def go():
        mgr = ChatManager()
        conn = FakeConn()
        await mgr.handle(conn, {"event": "unread", "data": {}})
        await mgr.apply_peer_chat(_inbound(node_id="unknown", text="who is this"))
        return conn

    conn = asyncio.run(go())
    delivered = [d for ev, d in conn.events() if ev == "message"]
    assert [d["text"] for d in delivered] == ["who is this"]
    assert delivered[0]["personId"] is None
    assert message_store.unread_counts() == {}


def test_dropped_conn_stops_receiving():
    _known_peer()

    async def go():
        mgr = ChatManager()
        conn = FakeConn()
        await mgr.handle(conn, {"event": "open", "data": {"personId": "p_alice"}})
        mgr.drop(conn)
        await mgr.apply_peer_chat(_inbound(text="after drop"))
        return conn

    conn = asyncio.run(go())
    assert not any(ev == "message" for ev, _ in conn.events())
