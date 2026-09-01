"""Running research subagents on a friend's node (`research/peer_subagent.py`).

The cheapest distribution in the repo — a subagent is already an isolated unit of
work and `ask_peer` is already an authenticated RPC — so most of what is worth
testing is the *refusals*: who is eligible, what happens when a peer says no, and
whether the answer carries enough provenance for the verification pass to grade it
honestly.

The rule that would be easiest to get wrong: a remote turn runs under the **peer's**
permission mode, which defaults to read-only. A remote subagent can therefore have
fewer tools than a local one, and anything that falls through must still produce a
finished wave.
"""

import asyncio


from backend.modules.research import peer_subagent


def _peer(node_id, caps=("agent",), trusted=True, status="connected"):
    from backend.modules.network.models import PeerInfo

    return PeerInfo(
        node_id=node_id,
        node_name=node_id,
        public_key="k",
        transport="direct",
        status=status,
        trusted=trusted,
        capabilities=list(caps),
    )


def _patch_peers(monkeypatch, peers):
    from backend.modules.network import hub as hub_mod

    monkeypatch.setattr(hub_mod.peer_hub, "list_peers", lambda: peers)


SPEC = {
    "name": "sources",
    "objective": "Find primary sources on X",
    "output_format": "bullets",
    "tool_guidance": "search the web",
    "boundaries": "no speculation",
    "max_tool_calls": 6,
}


# ---- eligibility ----------------------------------------------------------------


def test_a_trusted_connected_agent_peer_is_eligible(monkeypatch):
    _patch_peers(monkeypatch, [_peer("friend")])
    assert peer_subagent.eligible_peers() == ["friend"]


def test_an_untrusted_peer_is_not_eligible(monkeypatch):
    """Checked here as well as by the callee. Asking is cheap, but dispatching a
    research objective to a stranger's machine is not something to do on the
    assumption that they will refuse."""
    _patch_peers(monkeypatch, [_peer("stranger", trusted=False)])
    assert peer_subagent.eligible_peers() == []


def test_a_disconnected_peer_is_not_eligible(monkeypatch):
    _patch_peers(monkeypatch, [_peer("friend", status="disconnected")])
    assert peer_subagent.eligible_peers() == []


def test_a_peer_without_the_agent_capability_is_not_eligible(monkeypatch):
    _patch_peers(monkeypatch, [_peer("friend", caps=("hassault",))])
    assert peer_subagent.eligible_peers() == []


# ---- assignment ------------------------------------------------------------------


def test_assignment_is_capped(monkeypatch):
    """A research run must not be able to occupy every friend's agent, and the
    local machine is usually the fastest path anyway."""
    specs = [SPEC] * 10
    picked = peer_subagent.assign(specs, ["a", "b", "c"])
    assert len(picked) == peer_subagent.MAX_REMOTE_PER_WAVE


def test_assignment_round_robins(monkeypatch):
    picked = peer_subagent.assign([SPEC] * 4, ["a", "b"])
    assert list(picked.values()) == ["a", "b"]


def test_no_peers_means_no_assignment():
    assert peer_subagent.assign([SPEC], []) == {}


# ---- the prompt -------------------------------------------------------------------


def test_the_prompt_is_self_contained():
    """The remote side has no run context — no library handle, no plan, no sibling
    steps — so everything it needs has to be in the text."""
    prompt = peer_subagent.build_prompt(SPEC)
    assert "Find primary sources on X" in prompt
    assert "bullets" in prompt
    assert "no speculation" in prompt


def test_the_prompt_asks_for_read_only_work():
    """A remote turn runs under the peer's mode, which defaults to read-only.
    Asking for actions it cannot take would waste the turn."""
    prompt = peer_subagent.build_prompt(SPEC)
    assert "do not modify" in prompt.lower()


# ---- running remotely --------------------------------------------------------------


def _patch_ask(monkeypatch, reply):
    async def fake(node_id, prompt):
        return reply

    monkeypatch.setattr(peer_subagent, "_ask", fake)


def test_a_remote_answer_matches_the_local_shape(monkeypatch):
    """Same `(output, transcript, tokens)` triple as `engine.run_subagent_step`,
    so the runner's step machinery needs no branch."""
    _patch_ask(
        monkeypatch,
        {"answer": "Finding one.\nSOURCES:\n- https://example.com/a"},
    )
    result = asyncio.run(peer_subagent.run_remote(SPEC, "friend"))
    assert result is not None
    output, transcript, tokens = result
    assert output["name"] == "sources"
    assert "Finding one." in output["findings"]
    assert output["sources"]
    assert isinstance(transcript, list)
    assert tokens == 0


def test_the_answer_records_which_node_produced_it(monkeypatch):
    """The verification pass grades by *independent publisher*, so two peers
    citing one domain must count as one source. That arithmetic is only possible
    if the report says where it came from."""
    _patch_ask(monkeypatch, {"answer": "Finding.\nSOURCES:\n- https://x.test/1"})
    output, transcript, _ = asyncio.run(peer_subagent.run_remote(SPEC, "friend"))
    assert output["ran_on"] == "friend"
    assert transcript[0]["ran_on"] == "friend"


def test_remote_tokens_are_not_charged_to_this_run(monkeypatch):
    """They were spent on the peer's budget. Charging them here would throttle a
    run for work it did not pay for."""
    _patch_ask(monkeypatch, {"answer": "Finding."})
    _, _, tokens = asyncio.run(peer_subagent.run_remote(SPEC, "friend"))
    assert tokens == 0


def test_a_declining_peer_falls_back(monkeypatch):
    _patch_ask(monkeypatch, {"error": "remote agents are disabled here"})
    assert asyncio.run(peer_subagent.run_remote(SPEC, "friend")) is None


def test_a_timeout_falls_back(monkeypatch):
    _patch_ask(monkeypatch, {"error": "peer agent timed out"})
    assert asyncio.run(peer_subagent.run_remote(SPEC, "friend")) is None


def test_an_empty_answer_is_a_decline_not_a_finding(monkeypatch):
    """Recording an empty report as a completed step would let synthesis proceed
    believing this angle was covered."""
    _patch_ask(monkeypatch, {"answer": "   "})
    assert asyncio.run(peer_subagent.run_remote(SPEC, "friend")) is None


def test_a_raising_transport_falls_back(monkeypatch):
    async def boom(node_id, prompt):
        raise RuntimeError("socket died")

    monkeypatch.setattr(peer_subagent, "_ask", boom)
    assert asyncio.run(peer_subagent.run_remote(SPEC, "friend")) is None


# ---- timeout policy ----------------------------------------------------------------


def test_research_uses_its_own_timeout_not_the_chat_one():
    """A deep subagent turn is minutes of tool calls; `ask_peer`'s default is sized
    for a conversational question. Raising the global would make a chat user
    waiting on 'ask Rob's agent' inherit a ten-minute ceiling."""
    from backend.modules.network.agent_bridge import PEER_AGENT_TIMEOUT_S

    assert peer_subagent.PEER_SUBAGENT_TIMEOUT_S > PEER_AGENT_TIMEOUT_S


def test_the_loop_guard_is_carried(monkeypatch):
    """A peer that would have to come back to us to answer must not. `_ask` builds
    the same `origin_chain` `ask_peer` does."""
    sent = {}

    class FakeHub:
        class signer:
            node_id = "me"

        async def request(self, node, msg_type, data, timeout=None):
            sent.update(data)
            sent["timeout"] = timeout

            class R:
                data = {"ok": True, "text": "answer"}

            return R()

    from backend.modules.network import hub as hub_mod

    monkeypatch.setattr(hub_mod, "peer_hub", FakeHub())
    asyncio.run(peer_subagent._ask("friend", "prompt"))
    assert sent["origin_chain"] == ["me"]
    assert sent["timeout"] == peer_subagent.PEER_SUBAGENT_TIMEOUT_S


# ---- the setting -------------------------------------------------------------------


def test_distribution_is_off_by_default():
    """Dispatching research objectives to friends' machines is opt-in."""
    from backend.modules.settings.routes import get_value

    assert bool(get_value("research.distributeSubagents", False)) is False
