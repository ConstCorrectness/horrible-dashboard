import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.agent import roster
from backend.modules.flow import executor
from backend.modules.flow.models import Flow, FlowEdge, FlowNode


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


# --- routes (mirror the workspace store) -------------------------------------


def test_empty_collection(client: TestClient) -> None:
    res = client.get("/api/flows")
    assert res.status_code == 200
    assert res.json() == {"active": None, "flows": []}


def test_create_sets_active(client: TestClient) -> None:
    flow = client.post("/api/flows", json={"name": "Demo"}).json()
    assert flow["name"] == "Demo"
    assert flow["nodes"] == [] and flow["edges"] == []
    assert client.get("/api/flows").json()["active"] == flow["id"]


def test_upsert_roundtrips_graph(client: TestClient) -> None:
    nodes = [{"id": "t", "type": "trigger.prompt", "position": {}, "config": {}}]
    edges = [{"id": "e1", "source": "t", "target": "g"}]
    res = client.put(
        "/api/flows/demo", json={"name": "Demo", "nodes": nodes, "edges": edges}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "demo"
    assert [n["id"] for n in body["nodes"]] == ["t"]
    assert body["edges"][0]["source"] == "t"


def test_rename_does_not_clobber_graph(client: TestClient) -> None:
    nodes = [{"id": "t", "type": "trigger.prompt"}]
    client.put("/api/flows/demo", json={"name": "Demo", "nodes": nodes})
    res = client.put("/api/flows/demo", json={"name": "Renamed"})
    assert res.status_code == 200
    assert res.json()["name"] == "Renamed"
    assert [n["id"] for n in res.json()["nodes"]] == ["t"]  # graph intact


def test_delete_reassigns_active(client: TestClient) -> None:
    a = client.post("/api/flows", json={"name": "A"}).json()
    b = client.post("/api/flows", json={"name": "B"}).json()
    state = client.delete(f"/api/flows/{b['id']}").json()
    assert [f["id"] for f in state["flows"]] == [a["id"]]
    assert state["active"] == a["id"]


def test_bad_id_rejected(client: TestClient) -> None:
    assert client.put("/api/flows/..bad", json={"name": "x"}).status_code == 422


# --- executor ----------------------------------------------------------------


def test_topo_order_linear() -> None:
    flow = Flow(
        id="f",
        name="f",
        nodes=[
            FlowNode(id="a", type="trigger.prompt"),
            FlowNode(id="b", type="agent"),
            FlowNode(id="c", type="output.pane"),
        ],
        edges=[FlowEdge(source="a", target="b"), FlowEdge(source="b", target="c")],
    )
    assert executor._topo_order(flow) == ["a", "b", "c"]


def test_topo_order_detects_cycle() -> None:
    flow = Flow(
        id="f",
        name="f",
        nodes=[FlowNode(id="a", type="agent"), FlowNode(id="b", type="agent")],
        edges=[FlowEdge(source="a", target="b"), FlowEdge(source="b", target="a")],
    )
    with pytest.raises(ValueError, match="cycle"):
        executor._topo_order(flow)


class _FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)


class _FakeInfo:
    default_endpoint = "http://localhost:11434"
    dialect = "ollama"


class _FakeConfig:
    provider = "ollama"
    endpoint = None
    model = "test-model"


def test_run_flow_streams_node_and_edge_events(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # Seed a prompt -> agent -> output flow on disk.
    TestClient(app).put(
        "/api/flows/demo",
        json={
            "name": "Demo",
            "nodes": [
                {"id": "t", "type": "trigger.prompt", "config": {"prompt": "hi"}},
                {"id": "g", "type": "agent", "config": {}},
                {"id": "o", "type": "output.pane", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t", "target": "g"},
                {"id": "e2", "source": "g", "target": "o"},
            ],
        },
    )

    async def fake_loop(
        conn, run_id, messages, tools, info, endpoint, model, emit, **kw
    ):
        await emit("", "hello")  # streamed node_token
        return "AGENT_RESULT"

    monkeypatch.setattr(executor, "run_agent_loop", fake_loop)
    monkeypatch.setattr(executor, "_load_config", lambda: _FakeConfig())
    monkeypatch.setattr(executor, "_tools_for", lambda conn, prompt="": [])
    # An Agent node resolves its provider the same way `main` does (per-agent
    # settings, then the saved config), so the patch point is the roster helper.
    monkeypatch.setattr(
        roster,
        "resolve_provider",
        lambda config, agent_id="main": (_FakeInfo(), "http://x"),
    )

    conn = _FakeConn()
    asyncio.run(executor.run_flow(conn, "demo", "run1", None))

    events = [(o["event"], o["data"]) for o in conn.sent]
    names = [e for e, _ in events]
    assert names.count("node_started") == 3
    assert names.count("edge_fired") == 2
    assert "run_finished" in names
    assert any(e == "node_token" for e, _ in events)
    out = [d for e, d in events if e == "node_finished" and d["nodeId"] == "o"]
    assert out and out[0]["output"] == "AGENT_RESULT"  # agent result flows to output


def test_run_flow_tool_node_gates_and_relays(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    TestClient(app).put(
        "/api/flows/td",
        json={
            "name": "TD",
            "nodes": [
                {"id": "t", "type": "trigger.prompt", "config": {"prompt": "hi"}},
                {
                    "id": "k",
                    "type": "tool",
                    "config": {"tool": "stub.getValue", "args": {}},
                },
                {"id": "o", "type": "output.pane", "config": {}},
            ],
            "edges": [
                {"id": "a", "source": "t", "target": "k"},
                {"id": "b", "source": "k", "target": "o"},
            ],
        },
    )

    seen: dict = {}

    async def fake_gate(conn, run_id, call):
        seen["name"] = call.name
        seen["args"] = dict(call.arguments)
        return True

    async def fake_call(conn, run_id, name, args):
        return {"value": "42"}

    monkeypatch.setattr(executor, "_gate", fake_gate)
    monkeypatch.setattr(executor, "_call_frontend_tool", fake_call)
    monkeypatch.setattr(executor, "_load_config", lambda: _FakeConfig())

    conn = _FakeConn()
    asyncio.run(executor.run_flow(conn, "td", "r", None))

    events = [(o["event"], o["data"]) for o in conn.sent]
    out = [d for e, d in events if e == "node_finished" and d["nodeId"] == "k"]
    assert out and json.loads(out[0]["output"]) == {"value": "42"}  # relayed result
    assert seen["name"] == "stub.getValue"  # gated by the same engine
    assert seen["args"]["input"] == "hi"  # upstream payload injected as `input`


def test_tool_node_maps_upstream_to_configured_param(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # inputArg routes the upstream output into `path`, not a stray `input`.
    TestClient(app).put(
        "/api/flows/tm",
        json={
            "name": "TM",
            "nodes": [
                {
                    "id": "t",
                    "type": "trigger.prompt",
                    "config": {"prompt": "notes.txt"},
                },
                {
                    "id": "k",
                    "type": "tool",
                    "config": {"tool": "files.read", "args": {}, "inputArg": "path"},
                },
            ],
            "edges": [{"id": "a", "source": "t", "target": "k"}],
        },
    )

    seen: dict = {}

    async def fake_gate(conn, run_id, call):
        seen["args"] = dict(call.arguments)
        return True

    async def fake_call(conn, run_id, name, args):
        return {"content": "ok"}

    monkeypatch.setattr(executor, "_gate", fake_gate)
    monkeypatch.setattr(executor, "_call_frontend_tool", fake_call)
    monkeypatch.setattr(executor, "_load_config", lambda: _FakeConfig())

    conn = _FakeConn()
    asyncio.run(executor.run_flow(conn, "tm", "r", None))

    assert seen["args"].get("path") == "notes.txt"  # mapped to the real param
    assert "input" not in seen["args"]  # no stray input arg


def _seed_if_flow(prompt: str) -> None:
    TestClient(app).put(
        "/api/flows/iff",
        json={
            "name": "Iff",
            "nodes": [
                {"id": "t", "type": "trigger.prompt", "config": {"prompt": prompt}},
                {"id": "q", "type": "if", "config": {"op": "contains", "value": "yes"}},
                {"id": "a", "type": "output.pane", "config": {}},
                {"id": "b", "type": "output.pane", "config": {}},
            ],
            "edges": [
                {"id": "e1", "source": "t", "target": "q"},
                {"id": "e2", "source": "q", "target": "a", "sourceHandle": "true"},
                {"id": "e3", "source": "q", "target": "b", "sourceHandle": "false"},
            ],
        },
    )


def test_if_node_true_branch_prunes_false(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    _seed_if_flow("yes please")
    conn = _FakeConn()
    asyncio.run(executor.run_flow(conn, "iff", "r", None))

    events = [(o["event"], o["data"]) for o in conn.sent]
    finished = {d["nodeId"]: d for e, d in events if e == "node_finished"}
    skipped = {d["nodeId"] for e, d in events if e == "node_skipped"}
    fired = {d["edgeId"] for e, d in events if e == "edge_fired"}

    assert finished["q"]["branch"] == "true"
    assert finished["a"]["output"] == "yes please"  # true branch ran with the input
    assert "b" in skipped  # false branch pruned
    assert "e2" in fired and "e3" not in fired  # only the taken edge fired


def test_if_node_false_branch_prunes_true(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    _seed_if_flow("no thanks")
    conn = _FakeConn()
    asyncio.run(executor.run_flow(conn, "iff", "r", None))

    events = [(o["event"], o["data"]) for o in conn.sent]
    finished = {d["nodeId"]: d for e, d in events if e == "node_finished"}
    skipped = {d["nodeId"] for e, d in events if e == "node_skipped"}

    assert finished["q"]["branch"] == "false"
    assert "b" in finished and "a" in skipped
