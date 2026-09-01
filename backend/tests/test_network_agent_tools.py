"""The agent's surface over the peer fabric (`network/agent_tools.py`).

Three things are worth testing here and they are not the happy paths.

**The tools must stay loadable.** Every always-on backend tool costs schema bytes
on every turn; the core was cut from 34 tools to 11 against a reasoning cliff that
appears around 40 tools. Six new always-on tools would quietly undo a chunk of
that, and nothing at runtime would say so.

**A missing attribute must never match.** "Who has 8 GB of VRAM?" answered with a
node that never reported its VRAM is worse than an empty answer, and it is the
same three-state discipline `hardware/probe.py` exists to enforce.

**A refusal is an answer.** A peer declining to lend, or being offline, has to
come back as a reason the agent can act on — never as an exception that ends the
turn.
"""

import asyncio

import pytest

from backend.modules.network import agent_tools


def _peer(node_id, caps=None, status="connected", trusted=True):
    from backend.modules.network.models import PeerCapability, PeerInfo

    caps = caps or {}
    return PeerInfo(
        node_id=node_id,
        node_name=node_id,
        public_key="k",
        transport="direct",
        status=status,
        trusted=trusted,
        capabilities=sorted(caps),
        caps=[PeerCapability(id=k, attrs=v) for k, v in caps.items()],
    )


def _patch_peers(monkeypatch, peers):
    from backend.modules.network import hub as hub_mod

    monkeypatch.setattr(hub_mod.peer_hub, "list_peers", lambda: peers)


@pytest.fixture
def registered():
    from backend.sdk.registry import registry

    agent_tools.register_network_tools()
    return {
        name: tool
        for name, tool in registry.agent_tools.items()
        if name.startswith("network.")
    }


# ---- the budget ------------------------------------------------------------------


def test_all_six_tools_register(registered):
    assert set(registered) == {
        "network.survey",
        "network.measure_peer",
        "network.find_peers",
        "network.request_compute",
        "network.renew_lease",
        "network.release_lease",
    }


def test_every_tool_is_loadable_not_always_on(registered):
    """An ungrouped backend tool is in every turn's schema. Six of them would undo
    a meaningful part of the 34→11 core cut, silently."""
    assert all(tool.group == "network" for tool in registered.values())


def test_the_two_always_on_peer_verbs_are_untouched():
    """`list_peers` is the prerequisite `agent.ask_peer` documents, and `ask_peer`
    is the one peer verb a user names mid-conversation. Both stay in the core."""
    from backend.modules.agent.orchestrator import BACKEND_TOOL_NAMES

    assert "list_peers" in BACKEND_TOOL_NAMES
    assert "agent.ask_peer" in BACKEND_TOOL_NAMES
    assert not any(n.startswith("network.") for n in BACKEND_TOOL_NAMES)


def test_only_the_lease_tools_have_side_effects(registered):
    acting = {n for n, t in registered.items() if t.side_effect}
    assert acting == {
        "network.request_compute",
        "network.renew_lease",
        "network.release_lease",
    }


def test_the_group_has_a_description_and_keywords():
    """Without a description the catalog renders 'network tools'; without keywords
    the group is only reachable by an explicit `load_tools` round."""
    from backend.modules.agent.orchestrator import (
        _GROUP_DESCRIPTIONS,
        _GROUP_KEYWORDS,
    )

    assert "network" in _GROUP_DESCRIPTIONS
    assert "peer" in _GROUP_KEYWORDS["network"]


def test_no_keyword_is_claimed_by_two_groups():
    """Matching is substring, so a word in two groups preloads both and spends the
    tool budget twice for one intent. This caught `records`' bare "row" firing on
    "borrow"."""
    from backend.modules.agent.orchestrator import _GROUP_KEYWORDS

    collisions = [
        (group, other, mine)
        for mine in _GROUP_KEYWORDS["network"]
        for group, words in _GROUP_KEYWORDS.items()
        if group != "network"
        for other in words
        if mine in other or other in mine
    ]
    assert collisions == []


# ---- find_peers ------------------------------------------------------------------


def _find(**args):
    return asyncio.run(agent_tools.find_peers(args))


def test_find_peers_matches_a_capability(monkeypatch):
    _patch_peers(monkeypatch, [_peer("a", {"inference": {}}), _peer("b", {})])
    assert [p["node_id"] for p in _find(capability="inference")["peers"]] == ["a"]


def test_a_missing_attribute_never_matches(monkeypatch):
    """The important one. Treating absence as zero — or as a pass — answers "who
    has a GPU?" with a node that never said."""
    _patch_peers(monkeypatch, [_peer("a", {"inference": {"accelerator": "cuda"}})])
    assert _find(capability="inference", attr="vramMb", atLeast=8000)["peers"] == []


def test_a_numeric_threshold_filters(monkeypatch):
    _patch_peers(
        monkeypatch,
        [
            _peer("big", {"inference": {"vramMb": 12282}}),
            _peer("small", {"inference": {"vramMb": 2048}}),
        ],
    )
    found = _find(capability="inference", attr="vramMb", atLeast=8000)["peers"]
    assert [p["node_id"] for p in found] == ["big"]


def test_a_list_attribute_is_searched_by_membership(monkeypatch):
    """'Who can transcribe this?' — one query over `extras`, not a fourth
    mechanism."""
    _patch_peers(
        monkeypatch,
        [
            _peer("desktop", {"extras": {"installed": ["voice", "clip"]}}),
            _peer("laptop", {"extras": {"installed": ["clip"]}}),
        ],
    )
    found = _find(capability="extras", attr="installed", contains="voice")["peers"]
    assert [p["node_id"] for p in found] == ["desktop"]


def test_an_open_game_is_the_same_query(monkeypatch):
    _patch_peers(
        monkeypatch,
        [
            _peer("host", {"hassault": {"openMatches": 1}}),
            _peer("idle", {"hassault": {"openMatches": 0}}),
        ],
    )
    found = _find(capability="hassault", attr="openMatches", atLeast=1)["peers"]
    assert [p["node_id"] for p in found] == ["host"]


def test_a_disconnected_peer_is_not_a_match(monkeypatch):
    _patch_peers(monkeypatch, [_peer("gone", {"inference": {}}, status="disconnected")])
    assert _find(capability="inference")["peers"] == []


def test_the_count_searched_is_reported(monkeypatch):
    """So an empty list is not read as "nobody has one": a peer can be invisible
    here for reasons unrelated to what it offers."""
    _patch_peers(monkeypatch, [_peer("a", {}), _peer("b", {})])
    assert _find(capability="inference")["searched"] == 2


def test_find_peers_needs_a_capability():
    assert "error" in _find(capability="")


# ---- survey ----------------------------------------------------------------------


def test_survey_reports_peers_leases_and_the_lending_stance(monkeypatch):
    _patch_peers(monkeypatch, [_peer("friend", {"inference": {"vramMb": 8192}})])
    out = asyncio.run(agent_tools.survey({}))
    assert out["peers"][0]["caps"]["inference"]["vramMb"] == 8192
    assert "granted" in out["leases"] and "borrowed" in out["leases"]
    assert out["you"]["node_id"]


def test_the_lending_note_explains_a_refusal_before_it_happens(monkeypatch):
    """A user who turned lending on and still sees every request refused is the
    commonest confusion here — the policy defaults to `ask` and there is no
    approval UI. Two booleans do not say that; a sentence does."""
    from backend.modules.network import lease as lease_mod

    monkeypatch.setattr(lease_mod, "lending_enabled", lambda: True)
    monkeypatch.setattr(lease_mod, "lease_policy", lambda: "ask")
    assert "approval UI" in agent_tools._lending_note()

    monkeypatch.setattr(lease_mod, "lending_enabled", lambda: False)
    assert "allowComputeLending" in agent_tools._lending_note()


# ---- measure_peer ----------------------------------------------------------------


def test_measuring_an_unknown_peer_is_an_answer_not_a_crash(monkeypatch):
    _patch_peers(monkeypatch, [])
    assert "error" in asyncio.run(agent_tools.measure_peer({"node": "nobody"}))


def test_echo_mode_needs_a_node():
    assert "error" in asyncio.run(agent_tools.measure_peer({}))


def test_local_mode_needs_no_peer():
    """Separates "this peer is far away" from "this machine is slow"."""
    out = asyncio.run(agent_tools.measure_peer({"mode": "local"}))
    assert out["results"][0]["mode"] == "local"
    assert out["results"][0]["phases"]


# ---- the lease tools --------------------------------------------------------------


def test_a_refused_lease_comes_back_with_its_reason(monkeypatch):
    from backend.modules.network.lease import leases

    async def deny(*a, **kw):
        raise PermissionError("this node is serving 'gemma4:e2b'; ask for that")

    monkeypatch.setattr(leases, "request", deny)
    out = asyncio.run(
        agent_tools.request_compute({"node": "friend", "service": "llama"})
    )
    assert out["granted"] is False
    assert "gemma4" in out["reason"]


def test_an_unreachable_peer_is_also_a_reason(monkeypatch):
    from backend.modules.network.lease import leases

    async def boom(*a, **kw):
        raise KeyError("no connected peer")

    monkeypatch.setattr(leases, "request", boom)
    out = asyncio.run(
        agent_tools.request_compute({"node": "friend", "service": "llama"})
    )
    assert out["granted"] is False


def test_request_compute_needs_a_node_and_a_service():
    assert "error" in asyncio.run(agent_tools.request_compute({"node": "friend"}))


def test_a_grant_reports_the_tunnel_endpoint(monkeypatch):
    from backend.modules.network.lease import Borrowed, leases

    async def grant(hub, node, service, model=None, duration_s=None):
        return Borrowed(
            lease_id="L",
            node_id=node,
            service=service,
            model="llama-3.1-8b",
            expires_at=9e18,
            endpoint="http://127.0.0.1:51234",
        )

    monkeypatch.setattr(leases, "request", grant)
    out = asyncio.run(
        agent_tools.request_compute({"node": "friend", "service": "llama"})
    )
    assert out["granted"] is True
    assert out["endpoint"] == "http://127.0.0.1:51234"


def test_the_requested_duration_is_bounded():
    """The lender clamps regardless; this stops an obviously-wrong ask being sent
    at all."""
    assert agent_tools._minutes({"durationMinutes": 10_000}, 900.0) == (
        agent_tools.MAX_MINUTES * 60.0
    )
    assert agent_tools._minutes({"durationMinutes": "nonsense"}, 900.0) == 900.0
    assert agent_tools._minutes({}, 900.0) == 900.0


def test_releasing_gives_back_a_borrowed_lease(monkeypatch):
    from backend.modules.network.lease import Borrowed, leases

    released = []

    async def fake(lease_id, notify_peer=True):
        released.append(lease_id)
        leases.borrowed.pop(lease_id, None)

    leases.borrowed["L"] = Borrowed(
        lease_id="L", node_id="friend", service="llama", model=None, expires_at=9e18
    )
    monkeypatch.setattr(leases, "release_borrowed", fake)
    try:
        out = asyncio.run(agent_tools.release_lease({"leaseId": "L"}))
    finally:
        leases.borrowed.clear()
    assert out == {"ok": True, "released": "borrowed", "node": "friend"}
    assert released == ["L"]


def test_releasing_reclaims_a_lease_we_granted(monkeypatch):
    """One verb for both directions: "stop this" is one intent, and which side we
    are on is a fact the code can look up."""
    from backend.modules.network.lease import leases

    revoked = []

    async def fake(hub, lease_id, reason="revoked"):
        revoked.append(lease_id)
        return True

    lease = leases.grant("borrower", "llama", None, 60.0)
    monkeypatch.setattr(leases, "revoke", fake)
    try:
        out = asyncio.run(agent_tools.release_lease({"leaseId": lease.lease_id}))
    finally:
        leases.granted.clear()
    assert out["released"] == "granted"
    assert revoked == [lease.lease_id]


def test_releasing_an_unknown_lease_says_so():
    out = asyncio.run(agent_tools.release_lease({"leaseId": "nope"}))
    assert out["ok"] is False


def test_renewing_a_lease_we_do_not_hold_says_so():
    out = asyncio.run(agent_tools.renew_lease({"leaseId": "nope"}))
    assert out["ok"] is False
    assert "nope" in out["reason"]


def test_a_renewal_reads_the_expiry_back_from_the_lender():
    """The lender clamps to its own maximum and may refuse. Believing our own
    requested duration is how a borrower keeps sending work down a tunnel the
    lender has already closed."""
    from backend.modules.network.lease import Borrowed, leases

    class FakeHub:
        async def request(self, node, msg_type, data, timeout=None):
            class R:
                type = "compute_grant"
                data = {"expiresAt": 4242.0}

            return R()

    leases.borrowed["L"] = Borrowed(
        lease_id="L", node_id="friend", service="llama", model=None, expires_at=1.0
    )
    try:
        borrowed = asyncio.run(
            leases.renew_borrowed(FakeHub(), "L", duration_s=999_999.0)
        )
        assert borrowed.expires_at == 4242.0
    finally:
        leases.borrowed.clear()


def test_a_refused_renewal_raises_rather_than_extending():
    from backend.modules.network.lease import Borrowed, leases

    class FakeHub:
        async def request(self, node, msg_type, data, timeout=None):
            class R:
                type = "compute_deny"
                data = {"reason": "no such lease"}

            return R()

    leases.borrowed["L"] = Borrowed(
        lease_id="L", node_id="friend", service="llama", model=None, expires_at=1.0
    )
    try:
        with pytest.raises(PermissionError):
            asyncio.run(leases.renew_borrowed(FakeHub(), "L"))
        assert leases.borrowed["L"].expires_at == 1.0
    finally:
        leases.borrowed.clear()
