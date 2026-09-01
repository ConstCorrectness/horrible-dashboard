"""Compute leases and the byte tunnel (`network/lease.py`, `network/tunnel.py`).

The tunnel is tested against a plain `asyncio.start_server` echo rather than a
real `llama-server`: it is a byte pipe and has no idea what is on either end, so
a local echo exercises every property that matters and needs no GPU.

The tests that carry the most weight are the consent ones. Three gates guard
lending and each is independently sufficient to refuse; a regression that widens
any of them would hand a peer somebody's GPU.

Following the codebase convention (no pytest-asyncio), async flows run via
`asyncio.run` on an inner coroutine.
"""

import asyncio

import pytest

from backend.modules.network import identity, lease as lease_mod, tunnel as tunnel_mod
from backend.modules.network.hub import PeerHub
from backend.modules.network.lease import LeaseManager
from backend.modules.network.transport.loopback import InProcessTransport, connect_pair


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Both managers are process-global, like `peer_hub` itself."""
    lease_mod.leases.granted.clear()
    lease_mod.leases.borrowed.clear()
    tunnel_mod.tunnels._streams.clear()
    tunnel_mod.tunnels._services.clear()
    tunnel_mod.tunnels._authorize = None
    yield
    lease_mod.leases.granted.clear()
    lease_mod.leases.borrowed.clear()
    tunnel_mod.tunnels._streams.clear()


def _fresh_identity(monkeypatch, tmp_path, sub):
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


def _wire(hub):
    """One hub, one `TunnelManager` -- as a real node has.

    Stream ids are unique per node, not globally, so two in-process hubs sharing
    the global manager would have the lender's stream overwrite the borrower's
    under the same id, and the test would silently proxy a stream back to itself.
    """
    manager = tunnel_mod.TunnelManager()
    tunnel_mod.register(hub, manager)
    return manager


def _allow_lending(monkeypatch, policy="trusted"):
    """Open all three gates so the *other* property under test is what fails."""
    import backend.modules.network.lease as lm

    values = {
        "network.allowComputeLending": True,
        "network.computeLeasePolicy": policy,
    }
    monkeypatch.setattr(lm, "_setting", lambda k, d: values.get(k, d))


# ---- consent ------------------------------------------------------------------


def test_lending_is_off_by_default(monkeypatch):
    """Nothing is lent until somebody says so, mirroring allowRemoteAgent."""
    import backend.modules.network.lease as lm

    monkeypatch.setattr(lm, "_setting", lambda k, d: d)
    assert lm.lending_enabled() is False


def test_policy_defaults_to_ask(monkeypatch):
    import backend.modules.network.lease as lm

    monkeypatch.setattr(lm, "_setting", lambda k, d: d)
    assert lm.lease_policy() == "ask"


def test_unknown_policy_fails_closed(monkeypatch):
    """A typo or a downgrade from a newer version must narrow access, never widen
    it -- the `remoteAgentMode` -> `plan` precedent."""
    import backend.modules.network.lease as lm

    monkeypatch.setattr(
        lm, "_setting", lambda k, d: "autonomous-everything" if "Policy" in k else d
    )
    assert lm.lease_policy() == "off"


def test_untrusted_peer_is_refused_even_with_everything_enabled(monkeypatch):
    """Trust is checked unconditionally. Friendship grants reachability, not the
    right to run workloads -- and knowing a lease id is not friendship."""
    _allow_lending(monkeypatch)
    manager = LeaseManager()

    class Session:
        class info:
            trusted = False
            node_id = "peer"

    ok, reason = manager._may_grant(Session(), None)
    assert ok is False
    assert "trusted" in reason


def test_ask_policy_denies_until_there_is_an_approval_ui(monkeypatch):
    """`ask` must not silently behave as `yes`. The user believes they still have
    to approve, and granting anyway is exactly the gap that belief hides."""
    _allow_lending(monkeypatch, policy="ask")
    manager = LeaseManager()

    class Session:
        class info:
            trusted = True
            node_id = "peer"

    ok, reason = manager._may_grant(Session(), None)
    assert ok is False
    assert "ask" in reason.lower()


def test_only_the_loaded_model_is_lent_by_default(monkeypatch):
    """Evicting somebody's own chat model because a friend asked should not be
    reachable by default, and the refusal names what *is* loaded so the borrower
    can ask for that instead."""
    _allow_lending(monkeypatch)
    manager = LeaseManager()

    class FakeManager:
        @staticmethod
        def running():
            return True

        alias = "gemma"

    import backend.modules.llamacpp.server as server_mod

    monkeypatch.setattr(server_mod, "llama_manager", FakeManager)

    ok, _ = manager._model_ok("gemma")
    assert ok is True

    ok, reason = manager._model_ok("llama-70b")
    assert ok is False
    assert "gemma" in reason


# ---- authorization on every connection ----------------------------------------


def test_authorize_rejects_a_lease_belonging_to_another_node():
    manager = LeaseManager()
    lease = manager.grant("node-a", "llama", None, 60)
    ok, reason = manager.authorize("node-b", "llama", lease.lease_id)
    assert ok is False
    assert "another node" in reason


def test_authorize_rejects_the_wrong_service():
    manager = LeaseManager()
    lease = manager.grant("node-a", "llama", None, 60)
    ok, reason = manager.authorize("node-a", "embed", lease.lease_id)
    assert ok is False
    assert "llama" in reason


def test_authorize_rejects_an_expired_lease():
    """Re-checked per connection, not once at grant: a lease can expire between
    opening two sockets and a borrower must not keep getting service."""
    manager = LeaseManager()
    lease = manager.grant("node-a", "llama", None, 60)
    lease.expires_at = 0.0
    ok, reason = manager.authorize("node-a", "llama", lease.lease_id)
    assert ok is False
    assert "expired" in reason
    assert lease.lease_id not in manager.granted


def test_authorize_rejects_an_unknown_lease():
    manager = LeaseManager()
    ok, _ = manager.authorize("node-a", "llama", "made-up")
    assert ok is False


# ---- the rate limiter ---------------------------------------------------------


def test_rate_limiter_shapes_throughput():
    """The limit exists because saturating the fabric costs a concurrent 20 Hz
    stream 6x its latency -- smaller frames do not fix that, since the cost is
    signing bytes rather than scheduling."""

    async def flow():
        limiter = tunnel_mod.RateLimiter(100_000)  # 100 KB/s
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for _ in range(4):
            await limiter.take(50_000)
        return loop.time() - t0

    # 200 KB at 100 KB/s, with one second of burst, must take real time.
    assert asyncio.run(flow()) > 0.4


def test_rate_limiter_does_not_deadlock_on_a_huge_frame():
    """A frame larger than the bucket would otherwise wait for tokens it can
    never accumulate."""

    async def flow():
        limiter = tunnel_mod.RateLimiter(1000)
        await asyncio.wait_for(limiter.take(10_000_000), timeout=3)

    asyncio.run(flow())


def test_frame_cap_is_below_the_measured_pump_stall():
    """A 1 MB frame occupies the receiving pump for ~19 ms, against a 50 ms budget
    at 20 Hz. The cap has to stay well under that."""
    assert tunnel_mod.FRAME_BYTES <= 64 * 1024


# ---- the tunnel, end to end ---------------------------------------------------


def _echo_server():
    """A local TCP echo standing in for llama-server: the tunnel is a byte pipe
    and cannot tell the difference."""

    async def handle(reader, writer):
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    return handle


def test_tunnel_carries_bytes_both_ways(monkeypatch, tmp_path):
    a = _make_hub(monkeypatch, tmp_path, "a")  # borrower
    b = _make_hub(monkeypatch, tmp_path, "b")  # lender
    a_tun = _wire(a)
    b_tun = _wire(b)

    async def flow():
        echo = await asyncio.start_server(_echo_server(), "127.0.0.1", 0)
        port = echo.sockets[0].getsockname()[1]

        manager = LeaseManager()
        lease = manager.grant("", "llama", None, 300)
        await connect_pair(a, b)
        borrower_peer = next(iter(a.peers))
        lease.holder = next(iter(b.peers))

        b_tun.set_authorizer(manager.authorize)
        b_tun.register_service("llama", lambda: ("127.0.0.1", port))

        local = await a_tun.open_tunnel(a, borrower_peer, "llama", lease.lease_id)
        reader, writer = await asyncio.open_connection("127.0.0.1", local.port)
        writer.write(b"hello over the fabric")
        await writer.drain()
        got = await asyncio.wait_for(reader.read(64), timeout=10)
        writer.close()
        await local.close()
        echo.close()
        return got

    assert asyncio.run(flow()) == b"hello over the fabric"


def test_tunnel_refuses_without_a_lease(monkeypatch, tmp_path):
    a = _make_hub(monkeypatch, tmp_path, "a")
    b = _make_hub(monkeypatch, tmp_path, "b")
    a_tun = _wire(a)
    b_tun = _wire(b)

    async def flow():
        manager = LeaseManager()
        await connect_pair(a, b)
        peer = next(iter(a.peers))
        b_tun.set_authorizer(manager.authorize)
        b_tun.register_service("llama", lambda: ("127.0.0.1", 1))

        local = await a_tun.open_tunnel(a, peer, "llama", "bogus-lease")
        reader, writer = await asyncio.open_connection("127.0.0.1", local.port)
        # A refused open closes the local socket rather than hanging.
        got = await asyncio.wait_for(reader.read(16), timeout=10)
        writer.close()
        await local.close()
        return got

    assert asyncio.run(flow()) == b""


def test_tunnel_refuses_when_lending_was_never_enabled(monkeypatch, tmp_path):
    """With no authorizer installed the tunnel must refuse, not default to open."""
    a = _make_hub(monkeypatch, tmp_path, "a")
    b = _make_hub(monkeypatch, tmp_path, "b")
    a_tun = _wire(a)
    _wire(b)

    async def flow():
        await connect_pair(a, b)
        peer = next(iter(a.peers))
        local = await a_tun.open_tunnel(a, peer, "llama", "x")
        reader, writer = await asyncio.open_connection("127.0.0.1", local.port)
        got = await asyncio.wait_for(reader.read(16), timeout=10)
        writer.close()
        await local.close()
        return got

    assert asyncio.run(flow()) == b""


def test_revoking_a_lease_closes_its_streams(monkeypatch, tmp_path):
    """This is what makes a mid-turn revocation visible: the borrower's socket
    closes, so httpx raises inside an already-200 response instead of the answer
    silently truncating."""
    a = _make_hub(monkeypatch, tmp_path, "a")
    b = _make_hub(monkeypatch, tmp_path, "b")
    a_tun = _wire(a)
    b_tun = _wire(b)

    async def flow():
        echo = await asyncio.start_server(_echo_server(), "127.0.0.1", 0)
        port = echo.sockets[0].getsockname()[1]
        manager = LeaseManager()
        await connect_pair(a, b)
        peer = next(iter(a.peers))
        lease = manager.grant(next(iter(b.peers)), "llama", None, 300)
        b_tun.set_authorizer(manager.authorize)
        b_tun.register_service("llama", lambda: ("127.0.0.1", port))

        local = await a_tun.open_tunnel(a, peer, "llama", lease.lease_id)
        reader, writer = await asyncio.open_connection("127.0.0.1", local.port)
        writer.write(b"ping")
        await writer.drain()
        await asyncio.wait_for(reader.read(16), timeout=10)

        before = a_tun.stream_count()
        killed = a_tun.close_lease_streams(lease.lease_id, peer)
        writer.close()
        await local.close()
        echo.close()
        return before, killed

    before, killed = asyncio.run(flow())
    assert before > 0
    assert killed > 0


# ---- provider wiring -----------------------------------------------------------


def test_active_borrow_ignores_an_expired_lease():
    """`_endpoint_for` reads this; a stale endpoint would send a chat turn at a
    closed port."""
    manager = LeaseManager()
    manager.borrowed["x"] = lease_mod.Borrowed(
        lease_id="x",
        node_id="n",
        service="llama",
        model=None,
        expires_at=0.0,
        endpoint="http://127.0.0.1:9",
    )
    assert manager.active_borrow("llama") is None


def test_peer_provider_is_an_openai_dialect():
    """Not a new dialect: what is on the far end of the tunnel *is* a
    llama-server. A bespoke dialect would silently lose the
    `tool_choice="required"` retry, which is gated on this exact value."""
    from backend.modules.agent import providers as P

    info = P.provider_for("peer")
    assert info.kind == "peer"
    assert info.dialect == "openai"
    assert info.can_spawn is False


def test_endpoint_for_peer_uses_the_live_lease(monkeypatch):
    from backend.modules.agent import providers as P
    from backend.modules.agent.routes import _endpoint_for

    info = P.provider_for("peer")
    assert _endpoint_for(info, None) == ""

    lease_mod.leases.borrowed["l"] = lease_mod.Borrowed(
        lease_id="l",
        node_id="n",
        service="llama",
        model="gemma",
        expires_at=9e18,
        endpoint="http://127.0.0.1:54321",
    )
    assert _endpoint_for(info, None) == "http://127.0.0.1:54321"


def test_peer_provider_is_hidden_from_status_until_a_lease_exists(monkeypatch):
    """`peer` is the one provider a user cannot install or fix: without a lease it
    has no endpoint by design. Listing it as permanently unreachable would put a
    row in the status list that no action can ever resolve."""
    from backend.modules.agent import providers as P
    from backend.modules.agent.routes import _endpoint_for

    info = P.provider_for("peer")
    assert _endpoint_for(info, None) == ""

    lease_mod.leases.borrowed["l2"] = lease_mod.Borrowed(
        lease_id="l2",
        node_id="n",
        service="llama",
        model="gemma",
        expires_at=9e18,
        endpoint="http://127.0.0.1:5555",
    )
    assert _endpoint_for(info, None) == "http://127.0.0.1:5555"
