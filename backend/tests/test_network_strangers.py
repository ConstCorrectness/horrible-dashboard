"""Strangers: nodes that proved who they are but are not trusted.

A friend request arrives from someone who found you by `@username` and has never
paired with you. The handshake used to reject that node ("pairing required"), so
friending anyone not already paired by invite was impossible on any network. Now it
is admitted as a stranger, and these tests pin what that may and may not mean.
"""

from __future__ import annotations

import asyncio

from backend.modules.network import hub as hub_module
from backend.modules.network import protocol
from backend.modules.network.transport.loopback import connect_pair
from backend.tests.test_network_foundation import _make_hub

FRIEND_REQUEST = "social_friend_request"
PRIVATE = "agent_request"


def _pair(monkeypatch, tmp_path):
    hub_a, id_a = _make_hub(monkeypatch, tmp_path, "a", trust_mode="manual")
    hub_b, id_b = _make_hub(monkeypatch, tmp_path, "b", trust_mode="manual")
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
    return hub_a, id_a, hub_b, id_b


def _recorder(hub, types):
    seen: list[str] = []

    async def handler(_hub, session, env):
        seen.append(env.type)

    for msg_type in types:
        hub.register_handler(msg_type, handler)
    return seen


def test_a_stranger_can_deliver_a_friend_request_and_nothing_else(
    monkeypatch, tmp_path
):
    hub_a, id_a, hub_b, id_b = _pair(monkeypatch, tmp_path)
    seen_by_b = _recorder(hub_b, [FRIEND_REQUEST, PRIVATE])
    for hub in (hub_a, hub_b):
        hub.allow_from_strangers(FRIEND_REQUEST)

    async def go():
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        await hub_a.send_to(id_b, FRIEND_REQUEST, {"hello": True})
        # A's own gate refuses to send the private type to a stranger...
        refused_send = False
        try:
            await hub_a.send_to(id_b, PRIVATE, {"steal": True})
        except KeyError:
            refused_send = True
        # ...and B's dispatch gate refuses it even when a sender bypasses its own.
        session = hub_a._sessions[id_b]
        await session.send(await hub_a._signed(PRIVATE, id_b, {"steal": True}))
        await asyncio.sleep(0.05)
        return refused_send

    refused_send = asyncio.run(go())
    assert refused_send
    assert seen_by_b == [FRIEND_REQUEST]


def test_a_stranger_is_invisible_to_everything_that_uses_peers(monkeypatch, tmp_path):
    hub_a, id_a, hub_b, id_b = _pair(monkeypatch, tmp_path)
    events: list[tuple[str, dict]] = []
    hub_b.subscribe(lambda event, data: events.append((event, data)))

    async def go():
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        return (
            id_a in hub_b.peers,
            [p.node_id for p in hub_b.list_peers()],
            [p.node_id for p in hub_b.snapshot().peers],
            id_a in hub_b.strangers,
        )

    in_peers, listed, snapshot, is_stranger = asyncio.run(go())
    assert not in_peers and listed == [] and snapshot == []
    assert is_stranger
    # Browsers are not told a stranger is a peer.
    assert not [e for e in events if e[0] == "peer_update"]


def test_request_to_a_stranger_is_refused_unless_the_type_is_open(
    monkeypatch, tmp_path
):
    hub_a, id_a, hub_b, id_b = _pair(monkeypatch, tmp_path)

    async def go():
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        try:
            await hub_a.request(id_b, PRIVATE, {}, timeout=0.5)
        except KeyError:
            return True
        return False

    assert asyncio.run(go())


def test_accepting_the_friendship_upgrades_the_live_session(monkeypatch, tmp_path):
    hub_a, id_a, hub_b, id_b = _pair(monkeypatch, tmp_path)
    seen_by_b = _recorder(hub_b, [PRIVATE])
    events: list[str] = []
    hub_b.subscribe(lambda event, data: events.append(event))

    async def go():
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        hub_b.set_trusted(id_a, True)
        hub_a.set_trusted(id_b, True)
        await hub_a.send_to(id_b, PRIVATE, {})
        pong = await hub_a.request(id_b, protocol.PING, {}, timeout=2.0)
        await asyncio.sleep(0.05)
        return id_a in hub_b.peers, pong.type

    upgraded, pong = asyncio.run(go())
    assert upgraded and pong == protocol.PONG
    assert seen_by_b == [PRIVATE]
    assert "peer_update" in events


def test_revoking_trust_closes_the_session(monkeypatch, tmp_path):
    hub_a, id_a = _make_hub(monkeypatch, tmp_path, "a")
    hub_b, id_b = _make_hub(monkeypatch, tmp_path, "b")  # open-lan: trusted
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))

    async def go():
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        assert id_a in hub_b.peers
        hub_b.set_trusted(id_a, False)
        await asyncio.sleep(0.1)
        return id_a in hub_b._sessions

    assert asyncio.run(go()) is False


def test_an_idle_stranger_is_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(hub_module, "STRANGER_TTL_S", 0.1)
    hub_a, id_a, hub_b, id_b = _pair(monkeypatch, tmp_path)

    async def go():
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        before = id_a in hub_b.strangers
        await asyncio.sleep(0.3)
        return before, id_a in hub_b._sessions

    before, after = asyncio.run(go())
    assert before and not after


def test_strangers_are_capped(monkeypatch, tmp_path):
    monkeypatch.setattr(hub_module, "MAX_STRANGER_SESSIONS", 0)
    hub_a, id_a, hub_b, id_b = _pair(monkeypatch, tmp_path)

    async def go():
        try:
            await connect_pair(hub_a, hub_b)
        except Exception as exc:  # noqa: BLE001
            return str(exc)
        return ""

    assert "busy" in asyncio.run(go())


def test_a_dialer_does_not_trust_a_stranger_it_reached(monkeypatch, tmp_path):
    """Every successful dial used to write a trusted `known_peers` record. Reaching
    someone to send a friend request must not make them trusted."""
    from backend.modules.network import trust

    hub_a, id_a, hub_b, id_b = _pair(monkeypatch, tmp_path)

    async def go():
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        return trust.load_known_peers()

    known = asyncio.run(go())
    assert not (known.get(id_b) or {}).get("trusted")
    assert not (known.get(id_a) or {}).get("trusted")
