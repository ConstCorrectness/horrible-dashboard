"""Capability advertisement v2 (`network/capabilities.py`).

The load-bearing property is **backward compatibility**, in two directions at
once: `PeerHub.capabilities()` must keep returning the same `list[str]` it always
has (it is signed into `CommonsProfile` and re-served by a federated index), and a
peer that sends only `capabilities` and no `caps` — every Android build, every
node older than this change — must still work exactly as before.

Following the codebase convention (no pytest-asyncio), async flows run via
`asyncio.run` on an inner coroutine.
"""

import asyncio

import pytest

from backend.modules.network import capabilities, identity
from backend.modules.network.hub import PeerHub
from backend.modules.network.models import PeerCapability
from backend.modules.network.transport.loopback import InProcessTransport, connect_pair

#: What this node advertised before the registry existed. Pinned rather than
#: derived: the whole point is that the migration changed nothing observable.
LEGACY_CAPABILITIES = ["agent", "collab", "hassault", "share"]


@pytest.fixture(autouse=True)
def _clean_registry():
    """Every test starts from the built-ins only, and leaves them that way --
    the registry is process-global, so a test that registers a provider would
    otherwise leak it into every test after it."""
    capabilities.reset()
    yield
    capabilities.reset()


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


# ---- the frozen contract ------------------------------------------------------


def test_capabilities_is_byte_identical_to_before_the_registry():
    """The migration must be observably a no-op.

    `commons.build_profile` signs this list into `CommonsProfile`, which other
    nodes verify and a federated index re-serves. A changed element -- or even a
    changed order -- invalidates every profile already published.
    """
    assert PeerHub().capabilities() == LEGACY_CAPABILITIES


def test_capabilities_returns_plain_strings():
    """Not `PeerCapability` objects, not dicts. The lobby wire types this
    `string[]` and the Kotlin client parses it as one."""
    caps = PeerHub().capabilities()
    assert all(isinstance(c, str) for c in caps)


def test_builtins_are_registered_at_import_not_at_startup():
    """A bare `PeerHub()` must report the four without `start_network` having
    run -- tests construct one directly and assert against this list."""
    assert set(capabilities.ids()) == set(LEGACY_CAPABILITIES)


def test_signed_commons_profile_still_verifies(monkeypatch, tmp_path):
    from backend.modules.network.models import (
        CommonsProfile,
        canonical_profile_bytes,
    )

    me = _fresh_identity(monkeypatch, tmp_path, "commons")
    profile = CommonsProfile(
        node_id=me.node_id,
        public_key=me.public_key,
        display_name="n",
        agent_capabilities=PeerHub(signer=me).capabilities(),
    )
    profile.sig = me.sign(canonical_profile_bytes(profile))
    assert identity.verify(me.public_key, canonical_profile_bytes(profile), profile.sig)


# ---- the registry -------------------------------------------------------------


def test_a_provider_can_upgrade_a_builtin():
    """How hassault turns the static `hassault` into one that counts open
    matches: registering by the same id replaces it."""
    capabilities.register(
        "hassault", lambda: PeerCapability(id="hassault", attrs={"openMatches": 3})
    )
    snap = {c.id: c for c in capabilities.snapshot()}
    assert snap["hassault"].attrs == {"openMatches": 3}
    # ...without disturbing the flat list.
    assert capabilities.ids() == LEGACY_CAPABILITIES


def test_a_provider_returning_none_withdraws_the_capability():
    """ "Not offering this right now" must be expressible, or a node with no
    models would still advertise inference and fail every request."""
    capabilities.register("inference", lambda: None)
    assert "inference" not in capabilities.ids()


def test_providers_are_called_at_advertisement_time():
    """Attrs are live. If providers were called at registration, an open-match
    count would be frozen at boot -- which is worse than not having one."""
    calls = []

    def provider():
        calls.append(1)
        return PeerCapability(id="counter", attrs={"n": len(calls)})

    capabilities.register("counter", provider)
    first = {c.id: c for c in capabilities.snapshot()}["counter"]
    second = {c.id: c for c in capabilities.snapshot()}["counter"]
    assert first.attrs["n"] == 1
    assert second.attrs["n"] == 2


def test_a_failing_provider_does_not_sink_the_handshake():
    """One module's bad probe must cost that module's capability, not the node's
    ability to connect to anybody."""

    def boom():
        raise RuntimeError("probe exploded")

    capabilities.register("broken", boom)
    ids = capabilities.ids()
    assert "broken" not in ids
    assert ids == LEGACY_CAPABILITIES


def test_unserializable_attrs_are_dropped_not_raised():
    """These bytes get signed. A value `json.dumps` chokes on would raise inside
    `canonical_bytes` and fail the handshake, so the attr is discarded instead --
    a peer becoming unreachable because a module put a `datetime` in its attrs
    would be a baffling bug to chase."""
    import datetime as dt

    capabilities.register(
        "odd",
        lambda: PeerCapability(id="odd", attrs={"good": 1, "bad": dt.datetime.now()}),
    )
    cap = {c.id: c for c in capabilities.snapshot()}["odd"]
    assert cap.attrs == {"good": 1}


def test_snapshot_is_sorted():
    """A stable order means two identical presence broadcasts look identical."""
    capabilities.register("zzz", lambda: PeerCapability(id="zzz"))
    capabilities.register("aaa", lambda: PeerCapability(id="aaa"))
    ids = capabilities.ids()
    assert ids == sorted(ids)


# ---- wire compatibility -------------------------------------------------------


def test_caps_are_synthesized_when_a_peer_sends_none():
    """The Android client sends `capabilities` and no `caps`. So does every node
    older than this change. Both must keep working."""
    caps = capabilities.from_wire(None, ["mobile", "agent"])
    assert [c.id for c in caps] == ["mobile", "agent"]
    assert all(c.attrs == {} for c in caps)


def test_caps_are_used_when_present():
    caps = capabilities.from_wire(
        [{"id": "hassault", "version": 1, "attrs": {"openMatches": 2}}],
        ["hassault"],
    )
    assert len(caps) == 1
    assert caps[0].attrs["openMatches"] == 2


def test_malformed_caps_fall_back_rather_than_crashing():
    """A peer sending garbage in `caps` must degrade to the flat list, not take
    the handshake down with it."""
    caps = capabilities.from_wire(["not-a-dict", 42], ["agent"])
    assert [c.id for c in caps] == ["agent"]


def test_handshake_carries_both_fields(monkeypatch, tmp_path):
    a = _make_hub(monkeypatch, tmp_path, "a")
    b = _make_hub(monkeypatch, tmp_path, "b")
    capabilities.register(
        "hassault", lambda: PeerCapability(id="hassault", attrs={"openMatches": 1})
    )

    async def flow():
        await connect_pair(a, b)
        return a.peers[next(iter(a.peers))].info

    info = asyncio.run(flow())
    # The flat list every existing consumer reads, unchanged...
    assert info.capabilities == LEGACY_CAPABILITIES
    # ...and the rich form beside it.
    rich = {c.id: c for c in info.caps}
    assert rich["hassault"].attrs["openMatches"] == 1


def test_legacy_peer_without_caps_still_populates_both(monkeypatch, tmp_path):
    """Simulates an Android client: `capabilities` only, no `caps` key."""
    from backend.modules.network.models import PeerEnvelope
    from backend.modules.network.transport.loopback import LoopbackLink

    me = _fresh_identity(monkeypatch, tmp_path, "legacy")
    hub = PeerHub(signer=me)
    env = PeerEnvelope(
        type="hello",
        src=me.node_id,
        data={
            "node_name": "Pixel 9",
            "public_key": me.public_key,
            "capabilities": ["mobile"],
            "nonce": "n",
        },
    )
    info = hub._peer_info(
        env, me.public_key, LoopbackLink(asyncio.Queue()), trusted=True
    )
    assert info.capabilities == ["mobile"]
    assert [c.id for c in info.caps] == ["mobile"]


# ---- contributors -------------------------------------------------------------


def test_inference_capability_withdraws_when_there_is_nothing_to_offer(monkeypatch):
    """A node with no models must not advertise `inference`. Advertising it would
    show up in a peer's UI as an offer, and every request against it would fail."""
    from backend.modules.llamacpp import capability as inference

    inference.invalidate()
    monkeypatch.setattr(inference, "_model_attrs", lambda: {"modelCount": 0})
    monkeypatch.setattr(inference, "_accelerator_attrs", lambda: {"certain": True})
    assert inference.capability() is None


def test_inference_capability_reports_the_loaded_model(monkeypatch):
    """`serving` is the field the lease policy turns on -- granting against the
    already-hot model is what avoids evicting somebody's chat server."""
    from backend.modules.llamacpp import capability as inference

    inference.invalidate()
    monkeypatch.setattr(
        inference,
        "_model_attrs",
        lambda: {"modelCount": 2, "models": ["a", "b"], "serving": "gemma"},
    )
    monkeypatch.setattr(
        inference,
        "_accelerator_attrs",
        lambda: {"accelerator": "cuda", "vramMb": 16384, "certain": True},
    )
    cap = inference.capability()
    assert cap is not None
    assert cap.attrs["serving"] == "gemma"
    assert cap.attrs["accelerator"] == "cuda"
    inference.invalidate()


def test_inference_capability_never_claims_certainty_it_lacks(monkeypatch):
    """`hardware.probe` reports three states and this must preserve them: an
    absent accelerator and an unaskable one are different facts, and putting the
    flattened version on the wire is the fiction that module exists to prevent."""
    from backend.modules.llamacpp import capability as inference

    inference.invalidate()
    monkeypatch.setattr(
        inference, "_accelerator_attrs", lambda: {"certain": False, "ramMb": 32000}
    )
    monkeypatch.setattr(inference, "_model_attrs", lambda: {"modelCount": 1})
    cap = inference.capability()
    assert cap is not None
    assert cap.attrs["certain"] is False
    assert "accelerator" not in cap.attrs
    inference.invalidate()


def test_inference_capability_does_not_leak_filesystem_paths(monkeypatch):
    """Model *names*, never paths -- a path leaks the machine's directory layout
    and username to every peer, and a borrower picks a model by name anyway."""
    from backend.modules.llamacpp import capability as inference

    inference.invalidate()
    cap = inference.capability()
    if cap is None:  # no models on this machine; nothing to leak
        return
    blob = repr(cap.attrs)
    assert "/" not in blob.replace("\\/", "") or ":\\" not in blob
    inference.invalidate()


def test_hassault_capability_counts_open_matches(monkeypatch):
    from backend.modules.hassault import fabric

    monkeypatch.setattr(
        fabric.match_server,
        "listing",
        lambda: [
            {"id": "a", "players": 2},
            {"id": "b", "players": fabric.MAX_PLAYERS},
        ],
    )
    cap = fabric._capability()
    assert cap.id == "hassault"
    assert cap.attrs["matches"] == 2
    # The full one does not count: `browse_peers` uses this to skip peers with
    # nothing joinable.
    assert cap.attrs["openMatches"] == 1


# ---- presence debounce --------------------------------------------------------


def test_presence_announce_is_debounced(monkeypatch, tmp_path):
    """A filling hassault match would otherwise broadcast to every peer on every
    single join."""
    a = _make_hub(monkeypatch, tmp_path, "a")
    b = _make_hub(monkeypatch, tmp_path, "b")

    async def flow():
        await connect_pair(a, b)
        session = a.peers[next(iter(a.peers))]
        before = session.msgs_out
        for _ in range(5):
            await a.announce_presence()
        await asyncio.sleep(0)
        return session.msgs_out - before

    sent = asyncio.run(flow())
    # The first goes out immediately; the rest collapse into one trailing task
    # that has not fired yet.
    assert sent == 1


def test_presence_announce_reaches_peers(monkeypatch, tmp_path):
    a = _make_hub(monkeypatch, tmp_path, "a")
    b = _make_hub(monkeypatch, tmp_path, "b")

    async def flow():
        await connect_pair(a, b)
        capabilities.register(
            "hassault",
            lambda: PeerCapability(id="hassault", attrs={"openMatches": 7}),
        )
        await a.announce_presence()
        await asyncio.sleep(0.05)
        return b.peers[next(iter(b.peers))].info

    info = asyncio.run(flow())
    assert {c.id: c for c in info.caps}["hassault"].attrs["openMatches"] == 7


def test_presence_refreshes_both_fields(monkeypatch, tmp_path):
    from backend.modules.network.models import PeerEnvelope
    from backend.modules.network.transport.loopback import LoopbackLink

    me = _fresh_identity(monkeypatch, tmp_path, "presence")
    hub = PeerHub(signer=me)
    env = PeerEnvelope(
        type="hello",
        src=me.node_id,
        data={"public_key": me.public_key, "capabilities": ["agent"], "nonce": "n"},
    )
    from backend.modules.network.hub import PeerSession

    info = hub._peer_info(
        env, me.public_key, LoopbackLink(asyncio.Queue()), trusted=True
    )
    session = PeerSession(LoopbackLink(asyncio.Queue()), info)

    update = PeerEnvelope(
        type="presence",
        src=me.node_id,
        data={
            "capabilities": ["agent", "hassault"],
            "caps": [{"id": "hassault", "attrs": {"openMatches": 4}}],
        },
    )
    hub._apply_presence(session, update)
    assert session.info.capabilities == ["agent", "hassault"]
    assert {c.id: c for c in session.info.caps}["hassault"].attrs["openMatches"] == 4
