"""Peer Monitor tests: a heartbeat over a loopback pair measures RTT and the
per-session counters reflect real traffic."""

import asyncio

from backend.modules.network import identity
from backend.modules.network.hub import PeerHub
from backend.modules.network.monitor import PeerMonitor
from backend.modules.network.transport.loopback import InProcessTransport, connect_pair


def _fresh_hub(monkeypatch, tmp_path, sub):
    d = tmp_path / sub
    d.mkdir(exist_ok=True)
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(d))
    identity._cached_identity.cache_clear()
    me = identity.load_identity()
    from backend.modules.settings.routes import set_value

    set_value("network.trustMode", "open-lan")
    hub = PeerHub(signer=me)
    hub.set_transports([InProcessTransport()])
    return hub, me.node_id


def test_monitor_measures_rtt_and_counts_traffic(monkeypatch, tmp_path):
    hub_a, _ = _fresh_hub(monkeypatch, tmp_path, "a")
    hub_b, id_b = _fresh_hub(monkeypatch, tmp_path, "b")
    monitor = PeerMonitor(hub_a)

    async def go():
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.02)
        await monitor._tick()
        return monitor.snapshot()

    metrics = asyncio.run(go())
    assert len(metrics) == 1
    m = metrics[0]
    assert m.node_id == id_b
    # A ping/pong round-tripped, so RTT is populated and messages were counted.
    assert m.rtt_ms is not None and m.rtt_ms >= 0
    assert m.msgs_out >= 1
    assert m.msgs_in >= 1
    assert m.bytes_out > 0


def test_monitor_emits_peer_metrics(monkeypatch, tmp_path):
    hub_a, _ = _fresh_hub(monkeypatch, tmp_path, "a")
    hub_b, _ = _fresh_hub(monkeypatch, tmp_path, "b")
    monitor = PeerMonitor(hub_a)
    received: list[tuple[str, dict]] = []
    hub_a.subscribe(lambda event, data: received.append((event, data)))

    async def go():
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.02)
        await monitor._tick()

    asyncio.run(go())
    assert any(
        event == "peer_metrics" and isinstance(data.get("metrics"), list)
        for event, data in received
    )


def test_snapshot_empty_without_peers(monkeypatch, tmp_path):
    hub_a, _ = _fresh_hub(monkeypatch, tmp_path, "a")
    monitor = PeerMonitor(hub_a)
    assert monitor.snapshot() == []
