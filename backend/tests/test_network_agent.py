"""Agent-to-agent tests: a local agent asks a peer's agent and gets the answer back,
with the cross-peer permission boundary and loop/admission guards enforced.

Uses the in-process loopback transport plus a mocked provider so the callee's turn
returns a canned answer without a real model.
"""

import asyncio

import httpx

from backend.modules.agent import orchestrator
from backend.modules.agent.models import AgentConfig
from backend.modules.network import agent_bridge, identity, protocol
from backend.modules.network.hub import PeerHub
from backend.modules.network.transport.loopback import InProcessTransport, connect_pair


def _fresh_identity(monkeypatch, tmp_path, sub):
    d = tmp_path / sub
    d.mkdir(exist_ok=True)
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(d))
    identity._cached_identity.cache_clear()
    return identity.load_identity()


def _make_hub(monkeypatch, tmp_path, sub, **settings):
    me = _fresh_identity(monkeypatch, tmp_path, sub)
    from backend.modules.settings.routes import set_value

    set_value("network.trustMode", "open-lan")
    for k, v in settings.items():
        set_value(k, v)
    hub = PeerHub(signer=me)
    hub.set_transports([InProcessTransport()])
    return hub, me.node_id


def _mock_answer(monkeypatch, text: str):
    """Make any orchestrator turn answer `text` with no tool calls."""
    monkeypatch.setattr(
        orchestrator,
        "_load_config",
        lambda: AgentConfig(model="m", endpoint="http://ollama.test"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": text}}
        )

    monkeypatch.setattr(
        orchestrator,
        "instrumented_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_ask_peer_round_trip(monkeypatch, tmp_path):
    hub_a, id_a = _make_hub(monkeypatch, tmp_path, "a")
    hub_b, id_b = _make_hub(
        monkeypatch, tmp_path, "b", **{"network.allowRemoteAgent": True}
    )
    hub_b.register_handler(
        protocol.AGENT_REQUEST, agent_bridge.handle_remote_agent_request
    )
    _mock_answer(monkeypatch, "remote says hi")

    async def go():
        monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        return await agent_bridge.ask_peer(id_b, "hello there", hub=hub_a)

    result = asyncio.run(go())
    assert result == {"answer": "remote says hi"}


def test_ask_peer_rejected_when_disabled(monkeypatch, tmp_path):
    hub_a, id_a = _make_hub(monkeypatch, tmp_path, "a")
    # allowRemoteAgent defaults off on B.
    hub_b, id_b = _make_hub(monkeypatch, tmp_path, "b")
    hub_b.register_handler(
        protocol.AGENT_REQUEST, agent_bridge.handle_remote_agent_request
    )
    _mock_answer(monkeypatch, "should not be reached")

    async def go():
        monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        return await agent_bridge.ask_peer(id_b, "hello", hub=hub_a)

    result = asyncio.run(go())
    assert "error" in result
    assert "disabled" in result["error"]


def test_ask_peer_loop_guard(monkeypatch, tmp_path):
    hub_a, id_a = _make_hub(monkeypatch, tmp_path, "a")
    hub_b, id_b = _make_hub(
        monkeypatch, tmp_path, "b", **{"network.allowRemoteAgent": True}
    )
    hub_b.register_handler(
        protocol.AGENT_REQUEST, agent_bridge.handle_remote_agent_request
    )
    _mock_answer(monkeypatch, "unreached")

    async def go():
        monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        # B already appears in the origin chain → the request is a loop back to B.
        return await agent_bridge.ask_peer(id_b, "hi", origin_chain=[id_b], hub=hub_a)

    result = asyncio.run(go())
    assert "error" in result
    assert "loop" in result["error"]


def test_remote_turn_denies_side_effects(monkeypatch, tmp_path):
    """Under the default plan mode, the remote turn's gate denies any side effect —
    a remote agent can answer, never act on this machine."""
    from backend.modules.network.agent_bridge import RemoteAgentConn
    from backend.modules.network.hub import PeerHub

    # The gate only reads `force_mode`/`is_remote`; the hub and destination are the
    # relay path for streaming tokens, unused here.
    rconn = RemoteAgentConn(PeerHub(), "peer", "req-1", agent_bridge._remote_mode())

    class Call:
        name = "agent.ask_peer"  # a known side-effecting static tool
        arguments = {"peerId": "x", "prompt": "y"}

    # plan mode → DENY for side effects; is_remote also forbids prompting.
    allowed = asyncio.run(orchestrator._gate(rconn, "t", Call()))
    assert allowed is False


# ---- the callee's spans, returned with its answer -----------------------------------


def _ask_inside_a_tool_span(hub_a, id_b):
    """Ask B from inside an `execute_tool agent.ask_peer` span, the way a turn does,
    so a traceparent exists. Returns (result, the traceparent sent)."""
    from backend.modules.otel import tracing

    async def ask():
        with tracing.agent_span(
            turn_id="asker", agent_id="main", agent_name="", model="m", provider="x"
        ):
            with tracing.tool_span("agent.ask_peer", call_id="c1", round_no=0):
                tp = tracing.current_traceparent()
                return await agent_bridge.ask_peer(id_b, "hi", hub=hub_a), tp

    return ask


def _peer_pair(monkeypatch, tmp_path, **b_settings):
    from backend.modules.otel import peer_spans

    # Both nodes share this process's span store; see test_otel_peer_spans.py.
    monkeypatch.setattr(peer_spans, "_our_span_ids", lambda trace_id: set())
    hub_a, _ = _make_hub(monkeypatch, tmp_path, "a")
    hub_b, id_b = _make_hub(
        monkeypatch,
        tmp_path,
        "b",
        **{"network.allowRemoteAgent": True, **b_settings},
    )
    hub_b.register_handler(
        protocol.AGENT_REQUEST, agent_bridge.handle_remote_agent_request
    )
    _mock_answer(monkeypatch, "remote says hi")
    return hub_a, hub_b, id_b


def _run(hub_a, hub_b, id_b, monkeypatch, tmp_path):
    async def go():
        monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))
        await connect_pair(hub_a, hub_b)
        await asyncio.sleep(0.05)
        return await _ask_inside_a_tool_span(hub_a, id_b)()

    return asyncio.run(go())


def _from_peer(finished):
    return [s for s in finished if "horrible.peer.node_id" in s.resource.attributes]


def test_ask_peer_returns_the_callees_spans(monkeypatch, tmp_path):
    from backend.tests.otel_helpers import captured_spans

    hub_a, hub_b, id_b = _peer_pair(
        monkeypatch, tmp_path, **{"network.returnTraces": True}
    )
    with captured_spans(monkeypatch) as spans:
        result, tp = _run(hub_a, hub_b, id_b, monkeypatch, tmp_path)
        returned = _from_peer(spans.get_finished_spans())

    assert result == {"answer": "remote says hi"}
    assert returned, "the callee's spans never came back"
    # Stamped with the sender the fabric authenticated.
    assert {s.resource.attributes["horrible.peer.node_id"] for s in returned} == {id_b}
    _, trace_hex, parent_hex, _ = tp.split("-")
    assert all(format(s.context.trace_id, "032x") == trace_hex for s in returned)
    agent = next(s for s in returned if s.name.startswith("invoke_agent"))
    # B's turn hangs off A's ask_peer span: one trace, both sides.
    assert format(agent.parent.span_id, "016x") == parent_hex
    assert agent.attributes["horrible.source"] == "peer"
    assert any(s.name.startswith("chat") for s in returned)


def test_no_spans_come_back_without_the_callees_consent(monkeypatch, tmp_path):
    from backend.tests.otel_helpers import captured_spans

    # network.returnTraces defaults off.
    hub_a, hub_b, id_b = _peer_pair(monkeypatch, tmp_path)
    with captured_spans(monkeypatch) as spans:
        result, tp = _run(hub_a, hub_b, id_b, monkeypatch, tmp_path)
        returned = _from_peer(spans.get_finished_spans())
    assert tp is not None
    # The answer is unaffected: the trace is optional both ways.
    assert result == {"answer": "remote says hi"}
    assert returned == []
