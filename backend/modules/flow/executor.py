"""Flow executor: walk a saved graph and run each node, streaming execution
telemetry on the `flow` channel so the canvas lights up live.

This is deliberately a thin layer over the agent machinery: an **Agent node is one
`run_agent_loop`** (the shared orchestrator loop), and any tool an agent node calls
rides the existing `agent`-channel relay + permission `_gate` on the same `conn`
(see backend/modules/agent/orchestrator.py). The `flow` channel here carries only
node/edge telemetry. See docs/modules/flow-canvas.md.
"""

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from backend.modules.agent import providers as P
from backend.modules.agent.orchestrator import (
    _call_frontend_tool,
    _gate,
    _tool_context_size,
    _tool_max_tokens,
    _tool_temperature,
    _tool_top_p,
    _tools_for,
    run_agent_loop,
)
from backend.modules.agent.routes import _load_config
from backend.modules.flow.models import Flow, FlowNode
from backend.modules.flow.routes import load_flow
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

AGENT_SYSTEM_DEFAULT = (
    "You are an agent node inside a multi-agent flow. Act on the input from the "
    "previous node, using the available tools when needed, and produce a concise "
    "result for the next node."
)

# Running flow tasks keyed by runId, so a `stop` event can cancel one.
_runs: dict[str, asyncio.Task[None]] = {}


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "flow", "event": event, "data": data}


def _topo_order(flow: Flow) -> list[str]:
    """Kahn topological sort of the node ids. Raises ValueError on a cycle (Phase 1
    flows are acyclic; explicit loop nodes come later)."""
    indeg: dict[str, int] = {n.id: 0 for n in flow.nodes}
    adj: dict[str, list[str]] = defaultdict(list)
    for e in flow.edges:
        if e.source in indeg and e.target in indeg:
            adj[e.source].append(e.target)
            indeg[e.target] += 1
    queue = [nid for nid, d in indeg.items() if d == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for t in adj[nid]:
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    if len(order) != len(flow.nodes):
        raise ValueError("flow graph has a cycle")
    return order


async def handle_flow_message(conn: WsConnection, msg: dict[str, Any]) -> None:
    """Route an inbound `flow`-channel message from the browser."""
    event = msg.get("event")
    data = msg.get("data") or {}
    if event == "run":
        flow_id = str(data.get("flowId", ""))
        run_id = str(data.get("runId") or uuid.uuid4().hex[:8])
        raw_input = data.get("input")
        run_input = raw_input if isinstance(raw_input, str) else None
        task = asyncio.create_task(run_flow(conn, flow_id, run_id, run_input))
        _runs[run_id] = task
    elif event == "stop":
        run_id = str(data.get("runId", ""))
        task = _runs.get(run_id)
        if task is not None and not task.done():
            task.cancel()


async def run_flow(
    conn: WsConnection, flow_id: str, run_id: str, run_input: str | None
) -> None:
    """Execute one flow run: topo-walk the graph, run each node, and stream
    node/edge telemetry. Each node's output feeds its downstream edges."""
    flow = load_flow(flow_id)
    if flow is None:
        await conn.send_json(
            _evt("error", {"runId": run_id, "message": f"unknown flow '{flow_id}'"})
        )
        _runs.pop(run_id, None)
        return
    try:
        order = _topo_order(flow)
    except ValueError as exc:
        await conn.send_json(_evt("error", {"runId": run_id, "message": str(exc)}))
        _runs.pop(run_id, None)
        return

    nodes_by_id = {n.id: n for n in flow.nodes}
    edges_in: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    edges_out: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    for idx, e in enumerate(flow.edges):
        edges_in[e.target].append((idx, e))
        edges_out[e.source].append((idx, e))
    live: dict[int, bool] = {}  # edge index -> carries flow this run
    outputs: dict[str, str] = {}
    config = _load_config()

    try:
        for node_id in order:
            node = nodes_by_id[node_id]
            incoming = edges_in[node_id]
            # A node with inputs runs only if at least one incoming edge is live; a
            # branch the router didn't pick has no live input and is pruned.
            if incoming and not any(live.get(i) for i, _ in incoming):
                await conn.send_json(
                    _evt("node_skipped", {"runId": run_id, "nodeId": node_id})
                )
                continue
            await conn.send_json(
                _evt("node_started", {"runId": run_id, "nodeId": node_id})
            )
            payload = "\n\n".join(
                outputs.get(e.source, "")
                for i, e in incoming
                if live.get(i) and outputs.get(e.source)
            )
            branch: str | None = None
            try:
                if node.type == "if":
                    out, branch = _run_if_node(node, payload)
                else:
                    out = await _run_node(
                        conn, run_id, node, payload, run_input, config
                    )
            except Exception as exc:  # noqa: BLE001 — report any node failure to the UI
                await conn.send_json(
                    _evt(
                        "node_finished",
                        {
                            "runId": run_id,
                            "nodeId": node_id,
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                )
                await conn.send_json(
                    _evt("error", {"runId": run_id, "message": f"{node_id}: {exc}"})
                )
                return
            outputs[node_id] = out
            finished: dict[str, Any] = {
                "runId": run_id,
                "nodeId": node_id,
                "ok": True,
                "output": out,
            }
            if branch is not None:
                finished["branch"] = branch  # which handle an If node chose
            await conn.send_json(_evt("node_finished", finished))
            # Activate outgoing edges: a branch node lights only its chosen handle;
            # any other node lights all of its outgoing edges.
            for i, e in edges_out[node_id]:
                taken = branch is None or (e.sourceHandle or "") == branch
                live[i] = taken
                if taken:
                    await conn.send_json(
                        _evt(
                            "edge_fired",
                            {
                                "runId": run_id,
                                "edgeId": e.id,
                                "from": node_id,
                                "to": e.target,
                            },
                        )
                    )
        await conn.send_json(_evt("run_finished", {"runId": run_id}))
    except asyncio.CancelledError:
        await conn.send_json(_evt("error", {"runId": run_id, "message": "stopped"}))
        raise
    finally:
        _runs.pop(run_id, None)


def _eval_condition(node: FlowNode, payload: str) -> bool:
    """Evaluate an If node's condition against its input text."""
    op = str(node.config.get("op") or "non_empty")
    value = str(node.config.get("value") or "")
    text = payload or ""
    if op == "equals":
        return text.strip() == value
    if op == "contains":
        return value in text
    # default: non_empty
    return bool(text.strip())


def _run_if_node(node: FlowNode, payload: str) -> tuple[str, str]:
    """An If node passes its input through and selects the `true`/`false` output
    handle from its condition; the executor prunes the branch that wasn't chosen."""
    branch = "true" if _eval_condition(node, payload) else "false"
    return payload, branch


async def _run_node(
    conn: WsConnection,
    run_id: str,
    node: FlowNode,
    payload: str,
    run_input: str | None,
    config: Any,
) -> str:
    """Run one node and return its text output. Phase 1 node types only."""
    if node.type == "trigger.prompt":
        # An explicit run input overrides the node's configured prompt.
        if run_input is not None:
            return run_input
        return str(node.config.get("prompt", ""))
    if node.type == "output.pane":
        # Phase 1: the result is surfaced via node_finished/run output. A richer
        # sink (open a pane / write a buffer) is a documented follow-up.
        return payload
    if node.type == "agent":
        return await _run_agent_node(conn, run_id, node, payload, config)
    if node.type == "tool":
        return await _run_tool_node(conn, run_id, node, payload)
    logger.warning("flow: unknown node type %r — passing input through", node.type)
    return payload


async def _run_tool_node(
    conn: WsConnection, run_id: str, node: FlowNode, payload: str
) -> str:
    """A Tool node = one manifest tool call. It goes through the SAME permission
    gate and frontend relay an agent's tool call uses (`_gate` + `_call_frontend_tool`
    on this connection), so any pane's `agentTools`/commands are runnable here with no
    extra plumbing. The upstream node's output is offered as the `input` arg."""
    name = str(node.config.get("tool") or "")
    if not name:
        raise RuntimeError("tool node has no tool selected")
    args = dict(node.config.get("args") or {})
    # Map the upstream node's output onto the configured parameter (e.g. a
    # files.read node wires it into `path`). `inputArg` is chosen in the inspector;
    # when absent (legacy nodes) fall back to injecting it as `input`.
    input_arg = node.config.get("inputArg")
    if input_arg is None:
        if payload and "input" not in args:
            args["input"] = payload
    elif isinstance(input_arg, str) and input_arg and payload:
        args[input_arg] = payload
    call = SimpleNamespace(name=name, arguments=args)
    if not await _gate(conn, run_id, call):
        return "(denied by permission policy)"
    result = await _call_frontend_tool(conn, run_id, name, args)
    return result if isinstance(result, str) else json.dumps(result)


async def _run_agent_node(
    conn: WsConnection, run_id: str, node: FlowNode, payload: str, config: Any
) -> str:
    """An Agent node = one shared orchestrator loop. Streams answer tokens as
    `node_token` events scoped to this node; tool calls ride the agent-channel relay
    + gate on the same connection."""
    if config is None:
        raise RuntimeError("agent not configured")
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint
    model = str(node.config.get("model") or config.model)
    system = str(node.config.get("system") or AGENT_SYSTEM_DEFAULT)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": payload or "Begin."},
    ]
    tools = _tools_for(conn, prompt=payload)

    async def emit(reasoning: str, content: str) -> None:
        if reasoning:
            await conn.send_json(
                _evt(
                    "node_reasoning",
                    {"runId": run_id, "nodeId": node.id, "delta": reasoning},
                )
            )
        if content:
            await conn.send_json(
                _evt(
                    "node_token",
                    {"runId": run_id, "nodeId": node.id, "delta": content},
                )
            )

    return await run_agent_loop(
        conn,
        run_id,
        messages,
        tools,
        info,
        endpoint,
        model,
        emit,
        temperature=_tool_temperature(),
        context_size=_tool_context_size(),
        max_tokens=_tool_max_tokens(),
        top_p=_tool_top_p(),
    )
