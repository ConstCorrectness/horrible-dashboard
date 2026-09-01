"""Tests for the peer-fabric bench harness (`network/bench.py`).

Most of this runs over the `loopback` transport, which exercises the real
handshake, the real signing and the real dispatch path with no sockets.
Following the codebase convention (no pytest-asyncio), async flows run via
`asyncio.run` on an inner coroutine.

The one to read carefully is `test_slow_handler_blocks_the_pump`. It asserts the
*current* behaviour -- that a slow handler head-of-line-blocks every other
message on the link -- and it is expected to **fail once the dispatch policy
lands**, at which point the assertion inverts. That inversion is the proof the
fix worked, so the test is written to be flipped rather than deleted.
"""

import asyncio

import pytest

from backend.modules.network import bench, identity
from backend.modules.network.hub import PeerHub
from backend.modules.network.transport.loopback import InProcessTransport, connect_pair


def _fresh_identity(monkeypatch, tmp_path, sub):
    """Point identity at an isolated data dir and clear the lru cache so each hub
    gets a distinct keypair -- a shared signer would make every node the same node
    and the handshake's fingerprint check meaningless."""
    d = tmp_path / sub
    d.mkdir(exist_ok=True)
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(d))
    identity._cached_identity.cache_clear()
    return identity.load_identity()


def _make_hub(monkeypatch, tmp_path, sub):
    me = _fresh_identity(monkeypatch, tmp_path, sub)
    from backend.modules.settings.routes import set_value

    set_value("network.trustMode", "open-lan")
    hub = PeerHub(signer=me)
    hub.set_transports([InProcessTransport()])
    return hub


def _two_hubs(monkeypatch, tmp_path):
    """Hub A (the measurer) and hub B (which answers echoes)."""
    a = _make_hub(monkeypatch, tmp_path, "a")
    b = _make_hub(monkeypatch, tmp_path, "b")
    bench.register(b)
    return a, b


# ---- percentile arithmetic (no network needed) --------------------------------


def test_percentile_interpolates_a_known_distribution():
    values = [float(n) for n in range(1, 101)]  # 1..100
    assert bench.percentile(values, 0.0) == 1.0
    assert bench.percentile(values, 1.0) == 100.0
    # p50 of 1..100 sits between 50 and 51 under linear interpolation.
    assert bench.percentile(values, 0.5) == pytest.approx(50.5)
    assert bench.percentile(values, 0.99) == pytest.approx(99.01, abs=0.02)


def test_percentile_handles_degenerate_inputs():
    assert bench.percentile([], 0.5) == 0.0
    assert bench.percentile([7.0], 0.99) == 7.0


def test_phase_stats_reports_no_mean():
    """A mean over a bimodal blocked-pump distribution hides the bug the harness
    exists to find, so it must not be reportable by accident."""
    stats = bench.PhaseStats.of("verify", "x", [1.0, 1.0, 1.0, 100.0])
    assert "mean" not in stats.to_dict()
    assert stats.max_ms == 100.0
    assert stats.p50_ms == pytest.approx(1.0)


# ---- the ring -----------------------------------------------------------------


def test_ring_wraps_and_keeps_the_newest_samples():
    ring = bench.Ring(capacity=4)
    for n in range(6):
        ring.add(float(n))
    assert len(ring) == 4
    assert ring.values() == [2.0, 3.0, 4.0, 5.0]


def test_ring_preserves_order_before_wrapping():
    ring = bench.Ring(capacity=4)
    ring.add(1.0)
    ring.add(2.0)
    assert ring.values() == [1.0, 2.0]


# ---- local mode ---------------------------------------------------------------


def test_local_mode_times_every_phase(monkeypatch, tmp_path):
    _fresh_identity(monkeypatch, tmp_path, "solo")
    result = bench.run_local(iterations=50)

    phases = {p.phase for p in result.phases}
    assert phases == {"construct", "sign", "serialize", "deserialize", "verify"}
    assert result.transport == "none"
    # Signing and verifying are real elliptic-curve work; a zero here means the
    # phase was never actually measured.
    verify = next(p for p in result.phases if p.phase == "verify")
    assert verify.p50_ms > 0
    assert verify.count == 50


def test_local_mode_reports_the_real_encoded_size(monkeypatch, tmp_path):
    _fresh_identity(monkeypatch, tmp_path, "solo")
    small = bench.run_local(iterations=5, payload_bytes=16)
    large = bench.run_local(iterations=5, payload_bytes=4096)
    assert large.payload_bytes > small.payload_bytes + 4000


# ---- echo over loopback -------------------------------------------------------


def test_echo_round_trips_and_collects_samples(monkeypatch, tmp_path):
    a, b = _two_hubs(monkeypatch, tmp_path)

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        return await bench.run_echo(a, peer_id, count=20, transport="loopback"), peer_id

    result, peer_id = asyncio.run(flow())

    assert result.errors == 0
    assert result.rtt is not None
    assert result.rtt.count == 20
    assert result.mode == "echo"
    # The bench's own label, not PeerInfo.transport -- LoopbackLink reports
    # itself as "direct", so reading the peer's field files the run wrongly.
    assert result.transport == "loopback"
    assert result.node_id == peer_id


def test_echo_records_verify_phase_and_a_residual(monkeypatch, tmp_path):
    a, b = _two_hubs(monkeypatch, tmp_path)

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        return await bench.run_echo(a, peer_id, count=10, transport="loopback")

    result = asyncio.run(flow())
    phases = {p.phase for p in result.phases}
    assert "verify" in phases
    assert result.wire_residual_ms is not None
    assert result.wire_residual_ms >= 0


def test_probe_is_uninstalled_after_a_run(monkeypatch, tmp_path):
    """A probe left installed keeps allocating rings for traffic nobody is
    measuring, so a run must clear it even on the happy path."""
    from backend.modules.network import hub as hub_mod

    a, b = _two_hubs(monkeypatch, tmp_path)

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        await bench.run_echo(a, peer_id, count=3, transport="loopback")

    asyncio.run(flow())
    assert hub_mod._probe is None


def test_probe_off_is_a_true_no_op(monkeypatch, tmp_path):
    """With no probe installed the dispatch path must record nothing at all --
    the null check is the entire production cost of this module."""
    from backend.modules.network import hub as hub_mod

    a, b = _two_hubs(monkeypatch, tmp_path)
    probe = bench.BenchProbe()

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        await a.request(peer_id, bench.BENCH_ECHO, {"pad": "x"}, timeout=5)

    assert hub_mod._probe is None
    asyncio.run(flow())
    assert probe.stats() == []
    assert hub_mod._probe is None


def test_sweep_grows_the_payload(monkeypatch, tmp_path):
    a, b = _two_hubs(monkeypatch, tmp_path)

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        return await bench.run_sweep(
            a, peer_id, count=3, sizes=(64, 8192), transport="loopback"
        )

    results = asyncio.run(flow())
    assert len(results) == 2
    assert results[1].payload_bytes > results[0].payload_bytes
    assert all(r.errors == 0 for r in results)


def test_sustained_reports_victim_latency(monkeypatch, tmp_path):
    """The point of `sustained` is the victim stream, not the throughput -- a
    result without it would be measuring the wrong thing."""
    a, b = _two_hubs(monkeypatch, tmp_path)

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        return await bench.run_sustained(
            a, peer_id, duration_s=0.5, bulk_bytes=4096, transport="loopback"
        )

    result = asyncio.run(flow())
    assert result.mode == "sustained"
    assert result.victim is not None
    assert result.victim.count > 0
    assert result.bytes_sent > 0
    assert "MiB/s" in result.note


# ---- byte accounting ----------------------------------------------------------


def test_counters_measure_bytes_not_characters(monkeypatch, tmp_path):
    """The old counter was `len(str)`, which counts characters -- so a node name
    with an accent, or a chat message in any non-Latin script, was silently
    under-counted in the observability panel."""
    from backend.modules.network.hub import _wire_bytes
    from backend.modules.network.models import PeerEnvelope

    env = PeerEnvelope(type="x", src="n" * 16, data={"text": "café — 日本語"})
    encoded = env.model_dump_json()
    assert _wire_bytes(None, env) == len(encoded.encode("utf-8"))
    assert _wire_bytes(None, env) > len(encoded), "non-ASCII must cost extra bytes"


def test_transport_reported_size_is_preferred(monkeypatch, tmp_path):
    """When the transport knows the frame size the hub must use it rather than
    re-serializing the envelope purely to length it."""
    from backend.modules.network.hub import _wire_bytes
    from backend.modules.network.models import PeerEnvelope

    env = PeerEnvelope(type="x", src="n" * 16, data={"pad": "a" * 100})
    assert _wire_bytes(4242, env) == 4242


def test_direct_link_records_wire_sizes(monkeypatch, tmp_path):
    """The main transport must report both directions, or the hub silently falls
    back to the slow path on every message."""
    import asyncio as aio

    from backend.modules.network.models import PeerEnvelope
    from backend.modules.network.transport.direct import ServerPeerLink

    sent: list[str] = []

    class FakeWs:
        client = None

        async def send_text(self, raw):
            sent.append(raw)

        async def receive_text(self):
            return sent[-1]

    link = ServerPeerLink(FakeWs())
    env = PeerEnvelope(type="x", src="n" * 16, data={"text": "café"})

    async def flow():
        await link.send(env)
        await link.recv()

    aio.run(flow())
    expected = len(sent[0].encode("utf-8"))
    assert link.last_sent_bytes == expected
    assert link.last_recv_bytes == expected


def test_session_counters_track_real_traffic(monkeypatch, tmp_path):
    a, b = _two_hubs(monkeypatch, tmp_path)

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        session = a.peers[peer_id]
        before_out, before_in = session.bytes_out, session.bytes_in
        await bench.run_echo(
            a, peer_id, count=5, payload_bytes=1024, transport="loopback"
        )
        return session.bytes_out - before_out, session.bytes_in - before_in

    out, inbound = asyncio.run(flow())
    # Five ~1 KB echoes each way; the exact framing overhead is not the point,
    # the order of magnitude is.
    assert out > 5 * 1024
    assert inbound > 5 * 1024


# ---- head-of-line blocking ----------------------------------------------------


def _blocked_ms(monkeypatch, tmp_path, mode):
    """Time a trivial echo issued while a 500 ms handler of `mode` is running."""
    a, b = _two_hubs(monkeypatch, tmp_path)
    slow_type = "bench_slow"

    async def slow_handler(hub, session, env):
        await asyncio.sleep(0.5)
        await hub.send_to(session.info.node_id, slow_type, {}, re=env.msg_id)

    b.register_handler(slow_type, slow_handler, mode=mode)

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        slow = asyncio.create_task(a.request(peer_id, slow_type, {}, timeout=10))
        await asyncio.sleep(0.05)  # let the slow handler take the pump

        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await a.request(peer_id, bench.BENCH_ECHO, {"pad": "x"}, timeout=10)
        elapsed = (loop.time() - t0) * 1000
        await slow
        return elapsed

    return asyncio.run(flow())


def test_inline_handler_still_blocks_the_pump(monkeypatch, tmp_path):
    """`inline` is the default and is *supposed* to block -- that is what makes it
    the wrong mode for anything slow. Pinned so the default cannot drift silently
    into a detach and take the ordering guarantee with it."""
    assert _blocked_ms(monkeypatch, tmp_path, "inline") > 300


def test_detach_mode_frees_the_pump(monkeypatch, tmp_path):
    """The inversion of the Phase 1 blocking test: with the handler detached, an
    unrelated echo no longer waits 500 ms behind it."""
    assert _blocked_ms(monkeypatch, tmp_path, "detach") < 100


def test_serial_mode_frees_the_pump(monkeypatch, tmp_path):
    assert _blocked_ms(monkeypatch, tmp_path, "serial") < 100


def test_serial_mode_preserves_order(monkeypatch, tmp_path):
    """The reason `serial` exists rather than just using `detach`: detached tasks
    race, and two collab ops applied backwards is a corrupted document."""
    a, b = _two_hubs(monkeypatch, tmp_path)
    seen = []

    async def ordered_handler(hub, session, env):
        # A varying await makes a racing dispatch reorder these reliably.
        await asyncio.sleep(0.01 if env.data["n"] % 2 else 0.001)
        seen.append(env.data["n"])

    b.register_handler("bench_ordered", ordered_handler, mode="serial")

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        for n in range(30):
            await a.send_to(peer_id, "bench_ordered", {"n": n})
        # Let the worker drain.
        for _ in range(200):
            if len(seen) == 30:
                break
            await asyncio.sleep(0.01)

    asyncio.run(flow())
    assert seen == list(range(30)), f"serial dispatch reordered: {seen}"


def test_detached_handler_failure_is_logged_not_swallowed(
    monkeypatch, tmp_path, caplog
):
    """Moving work off the pump means its exceptions stop unwinding into `_pump`.
    If they were not caught and logged they would vanish entirely -- a worse
    failure than the blocking the move cures."""
    a, b = _two_hubs(monkeypatch, tmp_path)

    async def boom(hub, session, env):
        raise RuntimeError("handler exploded")

    b.register_handler("bench_boom", boom, mode="detach")

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        await a.send_to(peer_id, "bench_boom", {})
        await asyncio.sleep(0.1)
        # The link must survive a handler that raised.
        await a.request(peer_id, bench.BENCH_ECHO, {"pad": "x"}, timeout=5)

    with caplog.at_level("ERROR"):
        asyncio.run(flow())
    assert "handler exploded" in caplog.text


def test_serial_workers_are_cancelled_with_the_session(monkeypatch, tmp_path):
    """A worker parked on `queue.get()` would otherwise hold the session and its
    handler alive for the life of the process."""
    a, b = _two_hubs(monkeypatch, tmp_path)

    async def noop(hub, session, env):
        return None

    b.register_handler("bench_serial", noop, mode="serial")

    async def flow():
        await connect_pair(a, b)
        peer_id = next(iter(a.peers))
        await a.send_to(peer_id, "bench_serial", {})
        await asyncio.sleep(0.05)
        b_session = next(iter(b.peers.values()))
        assert b_session.serial_workers, "expected a worker to have been started"
        workers = list(b_session.serial_workers.values())
        await b.disconnect(b_session.info.node_id)
        await asyncio.sleep(0.05)
        return workers, b_session

    workers, session = asyncio.run(flow())
    assert all(w.cancelled() or w.done() for w in workers)
    assert session.serial_workers == {}
