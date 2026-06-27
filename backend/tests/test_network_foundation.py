"""Foundation tests for the peer fabric: identity, signing, and a two-hub handshake
over the in-process loopback transport (no sockets).

Following the codebase convention (no pytest-asyncio), async flows run via
`asyncio.run` on an inner coroutine.
"""

import asyncio

import pytest

from backend.modules.network import identity, protocol
from backend.modules.network.hub import PeerHub
from backend.modules.network.models import PeerEnvelope
from backend.modules.network.transport.loopback import InProcessTransport, connect_pair


def _fresh_identity(monkeypatch, tmp_path, sub):
    """Point identity/settings at an isolated data dir and clear the lru cache so
    each hub gets a distinct keypair."""
    d = tmp_path / sub
    d.mkdir(exist_ok=True)
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(d))
    identity._cached_identity.cache_clear()
    return identity.load_identity()


def _make_hub(monkeypatch, tmp_path, sub, *, trust_mode="open-lan"):
    me = _fresh_identity(monkeypatch, tmp_path, sub)
    from backend.modules.settings.routes import set_value

    set_value("network.trustMode", trust_mode)
    hub = PeerHub(signer=me)
    hub.set_transports([InProcessTransport()])
    return hub, me.node_id


def test_node_id_is_pubkey_fingerprint(monkeypatch, tmp_path):
    me = _fresh_identity(monkeypatch, tmp_path, "a")
    assert me.node_id == identity.fingerprint(me.public_key)
    assert len(me.node_id) == 16


def test_sign_and_verify_roundtrip(monkeypatch, tmp_path):
    me = _fresh_identity(monkeypatch, tmp_path, "a")
    env = PeerEnvelope(type="ping", src=me.node_id, data={"x": 1})
    signed = protocol.sign_envelope(env, me)
    assert protocol.verify_envelope(signed, me.public_key)
    # Tampering with the payload invalidates the signature.
    signed.data["x"] = 2
    assert not protocol.verify_envelope(signed, me.public_key)


def test_seen_guard_dedupes():
    guard = protocol.SeenGuard(capacity=4)
    assert guard.check("a") is True
    assert guard.check("a") is False
    assert guard.check("b") is True


# Assertions run INSIDE the loop: when asyncio.run() returns it cancels the peer
# pump tasks, whose teardown drops the sessions — so peer state must be captured
# while the loop is still live.


def test_two_hub_handshake(monkeypatch, tmp_path):
    hub_a, id_a = _make_hub(monkeypatch, tmp_path, "a")
    hub_b, id_b = _make_hub(monkeypatch, tmp_path, "b")
    assert id_a != id_b

    async def go():
        # B accepts under open-lan; trust reads the global data dir, so point it at B.
        monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        info = hub_a.peers[id_b].info
        return id_b in hub_a.peers, id_a in hub_b.peers, info.status, info.trusted

    a_has_b, b_has_a, status, trusted = asyncio.run(go())
    assert a_has_b and b_has_a
    assert status == "connected"
    assert trusted


def test_manual_mode_rejects_without_token(monkeypatch, tmp_path):
    hub_a, id_a = _make_hub(monkeypatch, tmp_path, "a")
    hub_b, id_b = _make_hub(monkeypatch, tmp_path, "b", trust_mode="manual")

    async def go():
        monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
        with pytest.raises(Exception):
            await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        return id_a in hub_b.peers, id_b in hub_a.peers

    b_has_a, a_has_b = asyncio.run(go())
    assert not b_has_a
    assert not a_has_b


def test_manual_mode_accepts_with_token(monkeypatch, tmp_path):
    from backend.modules.network import trust

    hub_a, id_a = _make_hub(monkeypatch, tmp_path, "a")
    hub_b, id_b = _make_hub(monkeypatch, tmp_path, "b", trust_mode="manual")

    async def go():
        # B mints a single-use invite token (stored under B's data dir).
        monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
        _invite, token, _exp = trust.make_invite("loopback", id_b)
        await connect_pair(hub_a, hub_b, token=token)
        await asyncio.sleep(0.05)
        return id_a in hub_b.peers, id_b in hub_a.peers

    b_has_a, a_has_b = asyncio.run(go())
    assert b_has_a and a_has_b


def test_request_reply_ping(monkeypatch, tmp_path):
    hub_a, id_a = _make_hub(monkeypatch, tmp_path, "a")
    hub_b, id_b = _make_hub(monkeypatch, tmp_path, "b")

    async def go():
        monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        return await hub_a.request(id_b, protocol.PING, {}, timeout=2.0)

    reply = asyncio.run(go())
    assert reply.type == protocol.PONG
