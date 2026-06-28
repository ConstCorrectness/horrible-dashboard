"""Tests for the WebRTC datachannel transport.

The `WebRtcLink` unit tests are duck-typed against fake channel/peer-connection
objects, so they run without aiortc installed. The end-to-end test needs the real
stack and is skipped (`importorskip`) when the optional `webrtc` extra isn't present;
it wires two transports to an in-memory signaler — no lobby server — and asserts the
full peer handshake completes over a real data channel.

Following the codebase convention (no pytest-asyncio), async flows run via
`asyncio.run` on an inner coroutine.
"""

import asyncio

import pytest

from backend.modules.network import identity, protocol
from backend.modules.network.hub import PeerHub
from backend.modules.network.models import PeerEnvelope
from backend.modules.network.transport.webrtc import WebRtcLink, WebRtcTransport


# ---- fakes (no aiortc) ------------------------------------------------------------


class _FakeChannel:
    """A stand-in RTCDataChannel: records sends, lets tests fire its events."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.readyState = "open"
        self._handlers: dict[str, object] = {}

    def on(self, event, fn=None):  # mirrors aiortc's decorator/direct-call dual form
        if fn is not None:
            self._handlers[event] = fn
            return fn

        def deco(f):
            self._handlers[event] = f
            return f

        return deco

    def send(self, data: str) -> None:
        self.sent.append(data)

    def fire(self, event: str, *args: object) -> None:
        handler = self._handlers.get(event)
        if handler is not None:
            handler(*args)  # type: ignore[operator]


class _FakePc:
    def __init__(self) -> None:
        self.connectionState = "connected"
        self.closed = False
        self._handlers: dict[str, object] = {}

    def on(self, event, fn=None):
        if fn is not None:
            self._handlers[event] = fn
            return fn

        def deco(f):
            self._handlers[event] = f
            return f

        return deco

    async def close(self) -> None:
        self.closed = True

    def fire(self, event: str, *args: object) -> None:
        handler = self._handlers.get(event)
        if handler is not None:
            handler(*args)  # type: ignore[operator]


# ---- WebRtcLink unit tests --------------------------------------------------------


def test_link_send_encodes_to_channel():
    async def go():
        ch, pc = _FakeChannel(), _FakePc()
        link = WebRtcLink(pc, ch, "peer123")
        await link.send(PeerEnvelope(type="ping", src="me", data={"x": 1}))
        return ch.sent

    sent = asyncio.run(go())
    assert len(sent) == 1
    decoded = protocol.decode(sent[0])
    assert decoded.type == "ping" and decoded.data["x"] == 1


def test_link_recv_decodes_inbound_message():
    async def go():
        ch, pc = _FakeChannel(), _FakePc()
        link = WebRtcLink(pc, ch, "peer123")
        ch.fire("message", protocol.encode(PeerEnvelope(type="pong", src="peer")))
        return await link.recv()

    got = asyncio.run(go())
    assert got.type == "pong"


def test_link_close_unblocks_recv_and_closes_pc():
    from backend.modules.network.transport.base import LinkClosed

    async def go():
        ch, pc = _FakeChannel(), _FakePc()
        link = WebRtcLink(pc, ch, "peer123")
        await link.close()
        with pytest.raises(LinkClosed):
            await link.recv()
        with pytest.raises(LinkClosed):
            await link.send(PeerEnvelope(type="ping", src="me"))
        return pc.closed

    assert asyncio.run(go()) is True


def test_link_connection_failure_closes():
    from backend.modules.network.transport.base import LinkClosed

    async def go():
        ch, pc = _FakeChannel(), _FakePc()
        link = WebRtcLink(pc, ch, "peer123")
        pc.connectionState = "failed"
        pc.fire("connectionstatechange")
        with pytest.raises(LinkClosed):
            await link.recv()

    asyncio.run(go())


# ---- setup guard ------------------------------------------------------------------


def test_build_transports_warns_without_aiortc(monkeypatch):
    from backend.modules.network import setup
    from backend.modules.network.transport import webrtc
    from backend.modules.settings.routes import set_value

    monkeypatch.setattr(webrtc, "AIORTC_AVAILABLE", False)
    set_value("network.enableWebRtc", True)
    try:
        transports = setup.build_transports()
    finally:
        set_value("network.enableWebRtc", False)
    assert not any(t.name == "webrtc" for t in transports)


# ---- end-to-end (real aiortc) -----------------------------------------------------


class _SignalEndpoint:
    """One node's view of an in-memory signaling bus, standing in for the lobby:
    `send_signal(to, payload)` routes to the target endpoint's handler, tagging
    `from` exactly as `lobby_server` does."""

    def __init__(self, bus: "_SignalBus", my_id: str) -> None:
        self._bus = bus
        self._my_id = my_id
        self._handler = None

    def set_signal_handler(self, cb) -> None:
        self._handler = cb

    async def send_signal(self, to: str, payload: dict) -> None:
        target = self._bus.endpoints.get(to)
        if target is not None and target._handler is not None:
            await target._handler(self._my_id, {**payload, "from": self._my_id})


class _SignalBus:
    def __init__(self) -> None:
        self.endpoints: dict[str, _SignalEndpoint] = {}

    def endpoint(self, node_id: str) -> _SignalEndpoint:
        ep = _SignalEndpoint(self, node_id)
        self.endpoints[node_id] = ep
        return ep


def _fresh_identity(monkeypatch, tmp_path, sub):
    d = tmp_path / sub
    d.mkdir(exist_ok=True)
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(d))
    identity._cached_identity.cache_clear()
    return identity.load_identity()


def test_webrtc_end_to_end(monkeypatch, tmp_path):
    pytest.importorskip("aiortc")
    from backend.modules.settings.routes import set_value

    me_a = _fresh_identity(monkeypatch, tmp_path, "a")
    me_b = _fresh_identity(monkeypatch, tmp_path, "b")
    id_a, id_b = me_a.node_id, me_b.node_id
    assert id_a != id_b

    set_value("network.trustMode", "open-lan")
    set_value("network.stunServer", "")  # host candidates only — offline + fast

    hub_a, hub_b = PeerHub(signer=me_a), PeerHub(signer=me_b)
    bus = _SignalBus()
    ta = WebRtcTransport(signaler=bus.endpoint(id_a))
    tb = WebRtcTransport(signaler=bus.endpoint(id_b))
    hub_a.set_transports([ta])
    hub_b.set_transports([tb])

    async def go():
        # Trust evaluation (B's acceptor) reads the global data dir; point it at B.
        monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
        await ta.start(hub_a)
        await tb.start(hub_b)
        info = await hub_a.connect(id_b, "webrtc")
        await asyncio.sleep(0.2)
        result = id_b in hub_a.peers, id_a in hub_b.peers, info.transport
        # Close the peer connections within the loop so aiortc's async teardown
        # doesn't fire after asyncio.run() tears the loop down.
        await hub_a.stop()
        await hub_b.stop()
        await asyncio.sleep(0.1)
        return result

    try:
        a_has_b, b_has_a, transport = asyncio.run(go())
    finally:
        set_value("network.stunServer", "stun.l.google.com:19302")

    assert a_has_b and b_has_a
    assert transport == "webrtc"
