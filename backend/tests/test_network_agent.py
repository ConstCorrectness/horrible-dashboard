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

    rconn = RemoteAgentConn(agent_bridge._remote_mode())

    class Call:
        name = "agent.ask_peer"  # a known side-effecting static tool
        arguments = {"peerId": "x", "prompt": "y"}

    # plan mode → DENY for side effects; is_remote also forbids prompting.
    allowed = asyncio.run(orchestrator._gate(rconn, "t", Call()))
    assert allowed is False
