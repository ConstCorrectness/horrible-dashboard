"""Agent orchestrator: a backend-resident tool-calling loop that drives the UI.

The turn runs over the shared `/ws` socket (bidirectional, per-browser) so the
model can call tools that execute in the *frontend* and feed results back without
any HTTP-request↔connection correlation. The model is the user's configured local
provider (Ollama, LM Studio, or vLLM — see providers.py), called with `tools`;
the calls relayed to the browser are app-level **layout** verbs in this first
slice. See docs/modules/agent-chat.md.
"""

import asyncio
import logging
import uuid
from typing import Any

import httpx

from backend.modules.agent import permission_store, permissions
from backend.modules.agent import providers as P
from backend.modules.agent.routes import _load_config
from backend.modules.telemetry.instrument import instrumented_client
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

# Guard against a model that never stops calling tools.
MAX_ROUNDS = 8
TOOL_TIMEOUT_S = 30.0
# A permission prompt waits on a human, so it gets a much longer leash.
APPROVAL_TIMEOUT_S = 300.0

SYSTEM_PROMPT = (
    "You are the orchestrator for horrible-dashboard, a dockable dashboard app. "
    "You arrange the user's screen by opening/closing panes, by splitting, "
    "resizing, moving, floating and maximizing them, and by managing workspaces, "
    "using the provided tools.\n"
    "Rules:\n"
    "- A 'pane' is a panel or widget shown on screen (e.g. Marketplace, Settings, "
    "Data flow, Backend status). To open/show/add something the user names, FIRST "
    "call list_available_panes to find the pane whose title matches, THEN call "
    "open_pane with that exact id. Do NOT treat it as a workspace.\n"
    "- To arrange ALREADY-OPEN panes (split/resize/move/float/maximize), FIRST "
    "call list_open_panes to get each pane's live instanceId. Geometry tools take "
    "the instanceId, NOT the pane type id. split_pane also needs a paneId (from "
    "list_available_panes) for the content to show in the new region.\n"
    "- Only use list_workspaces / create_workspace / switch_workspace when the "
    "user explicitly talks about workspaces or tabs.\n"
    "- Ids are not guessable; always discover them with a list_* tool first.\n"
    "- When the user asks ABOUT what's on screen, the layout, or a widget's "
    "contents, call list_open_panes first, then get_pane_context on the relevant "
    "pane(s), and answer from what they return — do not guess.\n"
    "- To change code in an open editor buffer (format, rewrite, fix), use "
    "editor.proposeEdit (NOT editor.applyEdit) so the user reviews the diff and "
    "accepts or declines it.\n"
    "- After acting, reply with one short sentence confirming what you did."
)


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }


# App-level layout verbs. Generic over ids (no enums) — the model discovers valid
# ids through the read tools, keeping the catalog frontend-owned.
LAYOUT_TOOLS: list[dict[str, Any]] = [
    _tool(
        "list_available_panes",
        "List every pane (panel or widget) that can be opened, with id and title. "
        "Call this to find a valid id before opening a pane.",
    ),
    _tool("list_workspaces", "List the named workspaces and which one is active."),
    _tool(
        "list_open_panes",
        "List the panes currently open in the active workspace, with each pane's "
        "type id, live instanceId, title, and whether it exposes agent-readable "
        "context. Use the instanceId with get_pane_context.",
    ),
    _tool(
        "get_pane_context",
        "Read a live pane's current state/selection snapshot (e.g. the active "
        "editor buffer's text, a file tree's selection). Use instanceId from "
        "list_open_panes.",
        {"instanceId": {"type": "string", "description": "Live pane instanceId"}},
        ["instanceId"],
    ),
    _tool(
        "open_pane",
        "Open a panel or widget as a pane in the active workspace.",
        {"id": {"type": "string", "description": "Pane id from list_available_panes"}},
        ["id"],
    ),
    _tool(
        "close_pane",
        "Close an open pane by its id.",
        {"id": {"type": "string", "description": "Id of an open pane"}},
        ["id"],
    ),
    _tool(
        "create_workspace",
        "Create a new named workspace and switch to it.",
        {"name": {"type": "string", "description": "Display name for the workspace"}},
        ["name"],
    ),
    _tool(
        "switch_workspace",
        "Switch to a workspace by its id (from list_workspaces).",
        {"id": {"type": "string"}},
        ["id"],
    ),
    _tool(
        "split_pane",
        "Split an open pane, opening another pane beside it. Prefer 'vertical' "
        "(panes side by side) or 'horizontal' (panes stacked); the concrete sides "
        "'left'/'right'/'above'/'below' are also accepted when the user wants the "
        "new pane on a specific side. Use instanceId from list_open_panes and "
        "paneId from list_available_panes.",
        {
            "instanceId": {"type": "string", "description": "Live pane to split"},
            "direction": {
                "type": "string",
                "enum": ["vertical", "horizontal", "left", "right", "above", "below"],
                "description": "vertical=side by side, horizontal=stacked",
            },
            "paneId": {
                "type": "string",
                "description": "Pane id (from list_available_panes) for the new region",
            },
        },
        ["instanceId", "direction", "paneId"],
    ),
    _tool(
        "resize_pane",
        "Resize the region holding an open pane. Sizes are in pixels; pass width "
        "and/or height. Use instanceId from list_open_panes.",
        {
            "instanceId": {"type": "string"},
            "width": {"type": "number", "description": "Target width in pixels"},
            "height": {"type": "number", "description": "Target height in pixels"},
        },
        ["instanceId"],
    ),
    _tool(
        "move_pane",
        "Move an open pane next to another open pane ('within' merges it into the "
        "reference's tab group). Both ids are instanceIds from list_open_panes.",
        {
            "instanceId": {"type": "string", "description": "Pane to move"},
            "reference": {"type": "string", "description": "Pane to move next to"},
            "direction": {
                "type": "string",
                "enum": ["left", "right", "above", "below", "within"],
            },
        },
        ["instanceId", "reference", "direction"],
    ),
    _tool(
        "float_pane",
        "Pop an open pane out into a floating window. instanceId from list_open_panes.",
        {"instanceId": {"type": "string"}},
        ["instanceId"],
    ),
    _tool(
        "dock_pane",
        "Dock a floating pane back into the layout. instanceId from list_open_panes.",
        {"instanceId": {"type": "string"}},
        ["instanceId"],
    ),
    _tool(
        "maximize_pane",
        "Maximize an open pane to fill the workspace. instanceId from list_open_panes.",
        {"instanceId": {"type": "string"}},
        ["instanceId"],
    ),
    _tool(
        "restore_pane",
        "Restore a maximized pane back to the normal layout.",
        {"instanceId": {"type": "string"}},
        ["instanceId"],
    ),
]


def _manifest_to_tools(serialized: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert the browser-pushed capability manifest (serialized AgentToolDecl /
    agent-command shapes) into provider tool definitions. Handlers never cross the
    wire, so only the schema is here."""
    tools: list[dict[str, Any]] = []
    for t in serialized:
        name = t.get("name")
        if not isinstance(name, str) or not name:
            continue
        params = t.get("params") or {"type": "object", "properties": {}, "required": []}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(t.get("description", "")),
                    "parameters": params,
                },
            }
        )
    return tools


def _tools_for(conn: WsConnection) -> list[dict[str, Any]]:
    """The model's tool list for a turn: static LAYOUT_TOOLS plus the connection's
    pushed dynamic tools, deduped by name (static wins)."""
    merged = list(LAYOUT_TOOLS)
    seen = {t["function"]["name"] for t in merged}
    for t in _manifest_to_tools(getattr(conn, "agent_tools", [])):
        if t["function"]["name"] in seen:
            continue
        merged.append(t)
        seen.add(t["function"]["name"])
    return merged


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "agent", "event": event, "data": data}


async def handle_agent_message(conn: WsConnection, msg: dict[str, Any]) -> None:
    """Route an inbound `agent`-channel message from the browser."""
    event = msg.get("event")
    data = msg.get("data") or {}
    if event == "manifest":
        tools = data.get("tools")
        conn.agent_tools = tools if isinstance(tools, list) else []
    elif event == "list_tools":
        # Introspection for the chat widget's `/tools` command: the full catalog the
        # model sees this turn (static layout verbs + the connection's pushed tools).
        layout_names = {t["function"]["name"] for t in LAYOUT_TOOLS}
        catalog = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "source": "layout"
                if t["function"]["name"] in layout_names
                else "widget",
            }
            for t in _tools_for(conn)
        ]
        await conn.send_json(_evt("tools", {"tools": catalog}))
    elif event == "ask":
        # Must not block the receive loop — the turn awaits tool_results that
        # arrive on that same loop. Run it detached.
        history = data.get("history")
        asyncio.create_task(
            run_agent_turn(
                conn,
                str(data.get("turnId", "")),
                str(data.get("prompt", "")),
                history if isinstance(history, list) else None,
            )
        )
    elif event == "tool_result":
        call_id = str(data.get("callId", ""))
        fut = conn.pending.pop(call_id, None)
        if fut is not None and not fut.done():
            fut.set_result(data)
    elif event == "approval_response":
        approval_id = str(data.get("approvalId", ""))
        fut = conn.pending_approvals.pop(approval_id, None)
        if fut is not None and not fut.done():
            fut.set_result(data)


async def _call_frontend_tool(
    conn: WsConnection, turn_id: str, name: str, args: dict[str, Any]
) -> Any:
    """Send a tool_call to the browser and await its tool_result."""
    call_id = uuid.uuid4().hex[:8]
    fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    conn.pending[call_id] = fut
    await conn.send_json(
        _evt(
            "tool_call",
            {"turnId": turn_id, "callId": call_id, "name": name, "args": args},
        )
    )
    try:
        data = await asyncio.wait_for(fut, timeout=TOOL_TIMEOUT_S)
    except TimeoutError:
        conn.pending.pop(call_id, None)
        return {"error": "tool timed out"}
    if data.get("ok"):
        return data.get("result")
    return {"error": data.get("error", "tool failed")}


def _tool_meta(conn: WsConnection, name: str) -> dict[str, Any] | None:
    """The pushed manifest entry for a tool, or None for layout/unknown tools."""
    for t in getattr(conn, "agent_tools", []):
        if t.get("name") == name:
            return t
    return None


def _default_rule(name: str, specifier: str | None) -> str:
    return f"{name}({specifier})" if specifier else name


async def _request_approval(
    conn: WsConnection,
    turn_id: str,
    name: str,
    specifier: str | None,
    mode: permissions.Mode,
) -> dict[str, Any]:
    """Prompt the browser to approve a gated call; await the user's decision."""
    approval_id = uuid.uuid4().hex[:8]
    fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    conn.pending_approvals[approval_id] = fut
    await conn.send_json(
        _evt(
            "approval_request",
            {
                "turnId": turn_id,
                "approvalId": approval_id,
                "tool": name,
                "specifier": specifier,
                "mode": mode.value,
            },
        )
    )
    try:
        return await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT_S)
    except TimeoutError:
        conn.pending_approvals.pop(approval_id, None)
        return {"decision": "deny"}


async def _gate(conn: WsConnection, turn_id: str, call: Any) -> bool:
    """Decide whether a relayed tool call may run. Read-only/layout tools pass
    straight through; side-effecting tools are evaluated against the permission
    rules + mode, prompting the user on an ASK and persisting a rule on
    'always allow'. Returns True to relay, False to deny."""
    meta = _tool_meta(conn, call.name)
    side_effect = bool(meta and meta.get("sideEffect"))
    if not side_effect:
        return True
    specifier = permissions.render_specifier(
        meta.get("specifierTemplate") if meta else None, call.arguments
    )
    mode = permission_store.load_mode()
    rules = permission_store.load_rules()
    decision = permissions.evaluate(call.name, specifier, side_effect, mode, rules)
    if decision is permissions.Decision.ALLOW:
        return True
    if decision is permissions.Decision.DENY:
        return False
    response = await _request_approval(conn, turn_id, call.name, specifier, mode)
    choice = response.get("decision")
    if choice == "allow_always":
        permission_store.add_rule(
            "allow", str(response.get("rule") or _default_rule(call.name, specifier))
        )
        return True
    return choice == "allow_once"


def _history_messages(history: list[Any] | None) -> list[dict[str, Any]]:
    """Sanitize prior-turn messages sent by the chat widget into the bare
    {role, content} pairs the providers accept. Only user/assistant text is kept
    (tool-call plumbing is per-turn and not replayed); anything malformed drops."""
    out: list[dict[str, Any]] = []
    for m in history or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            out.append({"role": role, "content": content})
    return out


async def run_agent_turn(
    conn: WsConnection,
    turn_id: str,
    prompt: str,
    history: list[Any] | None = None,
) -> None:
    """Drive one user turn: loop the configured provider's chat, relaying tool
    calls to the UI. The provider dialect (Ollama vs OpenAI-compatible) is hidden
    behind providers.chat / providers.tool_result_message. `history` carries prior
    user/assistant turns from the chat widget so the conversation is multi-turn
    while the backend stays stateless per turn."""
    config = _load_config()
    if config is None:
        await conn.send_json(
            _evt("error", {"turnId": turn_id, "message": "Agent not configured"})
        )
        return
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint
    tools = _tools_for(conn)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_history_messages(history),
        {"role": "user", "content": prompt},
    ]

    async def on_delta(reasoning: str, content: str) -> None:
        # Relay the model's streamed reasoning + answer tokens to the chat widget as
        # they arrive (the final `answer` event below stays authoritative).
        if reasoning:
            await conn.send_json(
                _evt("reasoning", {"turnId": turn_id, "delta": reasoning})
            )
        if content:
            await conn.send_json(_evt("token", {"turnId": turn_id, "delta": content}))

    try:
        async with instrumented_client(timeout=120) as client:
            for _ in range(MAX_ROUNDS):
                result = await P.chat_stream(
                    client, info, endpoint, config.model, messages, tools, on_delta
                )
                messages.append(result.assistant_message)
                if not result.tool_calls:
                    await conn.send_json(
                        _evt("answer", {"turnId": turn_id, "text": result.content})
                    )
                    await conn.send_json(_evt("done", {"turnId": turn_id}))
                    return
                for call in result.tool_calls:
                    if await _gate(conn, turn_id, call):
                        tool_result = await _call_frontend_tool(
                            conn, turn_id, call.name, call.arguments
                        )
                    else:
                        tool_result = {"error": "denied by permission policy"}
                    messages.append(P.tool_result_message(info, call, tool_result))
        await conn.send_json(
            _evt(
                "answer", {"turnId": turn_id, "text": "(stopped after too many steps)"}
            )
        )
        await conn.send_json(_evt("done", {"turnId": turn_id}))
    except httpx.HTTPError as exc:
        await conn.send_json(
            _evt(
                "error", {"turnId": turn_id, "message": f"{type(exc).__name__}: {exc}"}
            )
        )
