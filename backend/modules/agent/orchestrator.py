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
import re
import uuid
from typing import Any

import httpx

from backend.modules.agent import permission_store, permissions
from backend.modules.agent import providers as P
from backend.modules.agent.routes import _load_config
from backend.modules.settings.routes import get_value
from backend.modules.telemetry.instrument import instrumented_client
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

# Guard against a model that never stops calling tools.
MAX_ROUNDS = 8
TOOL_TIMEOUT_S = 30.0
# A permission prompt waits on a human, so it gets a much longer leash.
APPROVAL_TIMEOUT_S = 300.0

# Greedy decoding for tool-calling turns: at higher temperatures small local models
# narrate an action ("I'll call …") instead of emitting the structured tool call.
# Overridable via the settings store (no frontend change required).
DEFAULT_TOOL_TEMPERATURE = 0.0

# A weak model sometimes describes an action in prose without emitting the call. On
# the OpenAI dialect (which has a real `tool_choice`) we give it ONE forced retry
# when the text reads like an unemitted call — action phrasing or a named tool.
_ACTION_HINT = re.compile(
    r"\b(I['’]?ll|I will|I have|I'm going to|I am going to|let me|"
    r"calling|call the|use the|using the)\b",
    re.IGNORECASE,
)
_FORCE_TOOL_NUDGE = (
    "You described an action but did not emit a tool call. If an action is needed, "
    "emit the appropriate tool call now."
)


def _tool_temperature() -> float:
    """Sampling temperature for orchestrator turns (settings-overridable)."""
    value = get_value("agent.orchestrator.temperature", DEFAULT_TOOL_TEMPERATURE)
    try:
        return float(value)
    except (TypeError, ValueError):
        return DEFAULT_TOOL_TEMPERATURE


def _orchestrator_model(default: str) -> str:
    """Model for orchestrator turns. A separate override (settings-overridable)
    lets a stronger model drive tool calls than the one used for chat/autosuggest;
    blank falls back to the configured agent model."""
    value = get_value("agent.orchestrator.model", "")
    return value.strip() if isinstance(value, str) and value.strip() else default


def _looks_like_unemitted_tool_call(content: str, tools: list[dict[str, Any]]) -> bool:
    """Heuristic: the model answered in prose that reads like it meant to call a
    tool (action phrasing, or it names one of the available tools)."""
    if not content:
        return False
    if _ACTION_HINT.search(content):
        return True
    names = {t["function"]["name"] for t in tools}
    return any(name in content for name in names)


SYSTEM_PROMPT = (
    "You are the orchestrator for horrible-dashboard, a dockable dashboard app. "
    "You arrange the user's screen by opening/closing panes, by splitting, "
    "resizing, moving, floating and maximizing them, and by managing workspaces, "
    "using the provided tools.\n"
    "Rules:\n"
    "- The screen layout is composed of 'panes' (layout containers) hosting 'views' "
    "(registered panels or widgets, e.g. Marketplace, Settings, Data flow, Backend status). "
    "To open/show/add a view, FIRST call list_available_panes to find the view whose title "
    "matches, THEN call open_pane with that view ID. Do NOT treat it as a workspace.\n"
    "- To arrange active panes (split/resize/move/float/maximize), FIRST call list_open_panes "
    "to get each active pane's live instanceId. Geometry tools take the instanceId, NOT the "
    "view ID. split_pane also needs a view ID (paneId parameter, from list_available_panes) "
    "for the view content to show in the new split pane.\n"
    "- If the user refers to a pane via a title, file name, or instance ID (e.g., 'pane:main.py' or 'pane:editor.buffer#1'), "
    "match it against the title or instanceId of the open panes returned by list_open_panes to find the "
    "correct active instanceId to target.\n"
    "- If the user refers to a file via an absolute or relative path prefixed with '@' (e.g., '@absolute_path'), "
    "use that path directly with files/editor tools.\n"
    "- Only use list_workspaces / create_workspace / switch_workspace when the "
    "user explicitly talks about workspaces or tabs.\n"
    "- Ids are not guessable; always discover them with a list_* tool first.\n"
    "- When the user asks ABOUT what's on screen, the layout, or a view's "
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
        "List every view (panel or widget definition) that can be opened, with id and title. "
        "Call this to find a valid view ID before opening a pane.",
    ),
    _tool("list_workspaces", "List the named workspaces and which one is active."),
    _tool(
        "list_open_panes",
        "List the panes currently active in the active workspace, with each pane's "
        "view ID, live instanceId, title, and whether it exposes agent-readable "
        "context. Use the instanceId with get_pane_context.",
    ),
    _tool(
        "get_pane_context",
        "Read a live pane instance's current state/selection snapshot (e.g. the active "
        "editor buffer's text, a file tree's selection). Use instanceId from "
        "list_open_panes.",
        {"instanceId": {"type": "string", "description": "Active pane instanceId"}},
        ["instanceId"],
    ),
    _tool(
        "open_pane",
        "Open a view (panel or widget) in a pane in the active workspace.",
        {"id": {"type": "string", "description": "View ID from list_available_panes"}},
        ["id"],
    ),
    _tool(
        "close_pane",
        "Close an active pane by its instanceId or view ID.",
        {
            "id": {
                "type": "string",
                "description": "Instance ID or view ID of the pane to close",
            }
        },
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
        "Split an active pane, opening a new view beside it. 'left'/'right' give a "
        "vertical split (panes side by side), 'above'/'below' a horizontal one "
        "(panes stacked); the orientation aliases 'vertical'/'horizontal' are also "
        "accepted. Use instanceId from list_open_panes and paneId (view ID) from "
        "list_available_panes.",
        {
            "instanceId": {
                "type": "string",
                "description": "Live pane instance to split",
            },
            "direction": {
                "type": "string",
                "enum": ["vertical", "horizontal", "left", "right", "above", "below"],
                "description": "vertical=side by side, horizontal=stacked",
            },
            "paneId": {
                "type": "string",
                "description": "View ID (from list_available_panes) for the new split region",
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
        context = data.get("context")
        asyncio.create_task(
            run_agent_turn(
                conn,
                str(data.get("turnId", "")),
                str(data.get("prompt", "")),
                history if isinstance(history, list) else None,
                context if isinstance(context, dict) else None,
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


def _active_editor_message(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """A system message carrying the user's *focused* editor buffer, attached by the
    frontend to the turn. It hands the model the open code up front so it can alter
    it directly — modify this content, write the whole buffer back with
    editor.proposeEdit(uri=…) — instead of first discovering and reading the buffer
    via list_open_panes + get_pane_context (a dance weak local models often skip)."""
    if not isinstance(context, dict):
        return None
    snap = context.get("snapshot")
    if not isinstance(snap, dict):
        return None
    uri, content = snap.get("uri"), snap.get("content")
    # A real, addressable buffer only — an unsaved scratch buffer has no uri to edit.
    if not isinstance(uri, str) or uri == "(unsaved)" or not isinstance(content, str):
        return None
    title = snap.get("title") if isinstance(snap.get("title"), str) else uri
    parts = [
        f'The user is editing an open buffer "{title}" (uri: {uri}). Its current '
        "full content is between the markers:",
        "<<<BUFFER",
        content,
        "BUFFER>>>",
    ]
    selection = snap.get("selection")
    sel_text = selection.get("text") if isinstance(selection, dict) else None
    if isinstance(sel_text, str) and sel_text:
        parts.append(f"The user's current selection within it is: {sel_text!r}")
    parts.append(
        "When the user asks to alter/refactor/fix/format this code, modify THIS "
        "content and write the complete updated buffer back with "
        f'editor.proposeEdit(uri="{uri}"). Do not call list_open_panes or '
        "get_pane_context for it first — you already have its content here."
    )
    return {"role": "system", "content": "\n".join(parts)}


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
    context: dict[str, Any] | None = None,
) -> None:
    """Drive one user turn: loop the configured provider's chat, relaying tool
    calls to the UI. The provider dialect (Ollama vs OpenAI-compatible) is hidden
    behind providers.chat / providers.tool_result_message. `history` carries prior
    user/assistant turns from the chat widget so the conversation is multi-turn
    while the backend stays stateless per turn. `context` carries the user's focused
    pane snapshot (currently the open editor buffer) so the model can act on what
    the user is looking at without a discovery round-trip."""
    config = _load_config()
    if config is None:
        await conn.send_json(
            _evt("error", {"turnId": turn_id, "message": "Agent not configured"})
        )
        return
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint
    model = _orchestrator_model(config.model)
    tools = _tools_for(conn)
    editor_msg = _active_editor_message(context)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_history_messages(history),
        # The focused buffer goes right before the user turn so it's the freshest
        # context the model sees (and isn't diluted by prior conversation).
        *([editor_msg] if editor_msg else []),
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

    temperature = _tool_temperature()
    forced_retry_used = False
    try:
        async with instrumented_client(timeout=120) as client:
            for _ in range(MAX_ROUNDS):
                result = await P.chat_stream(
                    client,
                    info,
                    endpoint,
                    model,
                    messages,
                    tools,
                    on_delta,
                    temperature=temperature,
                )
                messages.append(result.assistant_message)

                # Weak models sometimes narrate an action without emitting the call.
                # On the OpenAI dialect, force one retry with tool_choice=required.
                if (
                    not result.tool_calls
                    and not forced_retry_used
                    and info.dialect == "openai"
                    and _looks_like_unemitted_tool_call(result.content, tools)
                ):
                    forced_retry_used = True
                    messages.append({"role": "system", "content": _FORCE_TOOL_NUDGE})
                    result = await P.chat_stream(
                        client,
                        info,
                        endpoint,
                        model,
                        messages,
                        tools,
                        on_delta,
                        temperature=temperature,
                        tool_choice="required",
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
