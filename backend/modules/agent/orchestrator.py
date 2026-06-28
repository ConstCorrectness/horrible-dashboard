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
from collections.abc import Awaitable, Callable
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


def _tool_context_size() -> int | None:
    """Context size limit (num_ctx) for orchestrator turns (settings-overridable)."""
    value = get_value("agent.orchestrator.contextSize", None)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tool_max_tokens() -> int | None:
    """Max output tokens for orchestrator turns (settings-overridable)."""
    value = get_value("agent.orchestrator.maxTokens", None)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tool_top_p() -> float | None:
    """Top P sampling for orchestrator turns (settings-overridable)."""
    value = get_value("agent.orchestrator.topP", None)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    "- Tools are organized into GROUPS. You start with the core layout tools; other "
    "capabilities (files, editor, terminal, …) live in groups that aren't shown until "
    "loaded. If a task needs a capability you don't see, call list_tool_groups to "
    "discover what's available, then load_tools([...]) to enable the group(s) before "
    "using their tools.\n"
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
        "Split an active pane. 'left'/'right' give a vertical split (panes side by "
        "side), 'above'/'below' a horizontal one (panes stacked); the orientation "
        "aliases 'vertical'/'horizontal' are also accepted. Use instanceId from "
        "list_open_panes. paneId (a view ID from list_available_panes) is OPTIONAL — "
        "omit it to duplicate the pane's own view into the new region (the usual "
        "'just split this pane'); pass it only to put a different view there.",
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
                "description": "Optional view ID for the new region; omit to duplicate the split pane's view",
            },
        },
        ["instanceId", "direction"],
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


# Backend-resolved tools for the distributed peer fabric. Unlike LAYOUT_TOOLS (which
# relay to the browser), these execute in the backend against the process-global
# PeerHub, so agent-to-agent works with no browser handler. See modules/network.
PEER_TOOLS: list[dict[str, Any]] = [
    _tool(
        "list_peers",
        "List the connected peer nodes (other users' agents) you can ask, with "
        "node_id, name, and capabilities. Call this before agent.ask_peer.",
    ),
    _tool(
        "agent.ask_peer",
        "Ask another user's agent a question and get its answer back. Use list_peers "
        "first to find a peerId. The remote agent answers on its own machine under "
        "its owner's permissions.",
        {
            "peerId": {"type": "string", "description": "node_id from list_peers"},
            "prompt": {"type": "string", "description": "the question to ask"},
        },
        ["peerId", "prompt"],
    ),
]

# Names dispatched in the backend (not relayed to the browser).
BACKEND_TOOL_NAMES = {t["function"]["name"] for t in PEER_TOOLS}

# Side-effect metadata for static backend tools (the browser manifest carries this
# for frontend tools; static tools declare it here so the gate can see it).
# agent.ask_peer reaches another machine, so it's gated; list_peers is read-only.
_STATIC_TOOL_META: dict[str, dict[str, Any]] = {
    "agent.ask_peer": {
        "name": "agent.ask_peer",
        "sideEffect": True,
        "specifierTemplate": "{peerId}",
    },
}


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


# ---- Hierarchical / progressively-disclosed tools ---------------------------------
#
# Rather than flatten every tool into one list (and silently prune past a model's
# capacity), tools are organized into GROUPS by name prefix. The model always sees a
# small CORE — the layout verbs, the peer tools, and two META tools — and pulls in a
# group's tools on demand with load_tools. The orchestrator recomputes the tool list
# each round, so a loaded group's tools are injected into the next model call. This
# scales past the ~38-tool ceiling local reasoning models choke on, with nothing ever
# dropped — an unloaded tool is one load_tools call away, not gone.

META_TOOLS: list[dict[str, Any]] = [
    _tool(
        "list_tool_groups",
        "List the tool groups (capability categories) available beyond the core tools "
        "you can already see — each with a short description and a tool count. Call this "
        "to discover capabilities (files, editor, terminal, …) before using them.",
    ),
    _tool(
        "load_tools",
        "Enable one or more tool groups so their tools become callable. Pass group names "
        "from list_tool_groups; the group's tools appear on your next step.",
        {
            "groups": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Group names to load, e.g. ['files','editor'].",
            }
        },
        ["groups"],
    ),
]

META_TOOL_NAMES = {t["function"]["name"] for t in META_TOOLS}

# Cap kept only as a safety backstop now that groups load on demand.
TOOL_BUDGET = 38

# Human-readable blurbs for known groups; unknown groups get a generic fallback.
_GROUP_DESCRIPTIONS: dict[str, str] = {
    "files": "Browse, read, search, create, and edit files in the workspace.",
    "editor": "Inspect and modify open editor buffers (read, propose edits, format, rename).",
    "terminal": "Run shell commands and manage terminal sessions.",
    "visualizer": "Render Canvas / Three.js / Babylon.js animations and stream Pygame frames.",
    "vectordb": "Local vector database: upsert documents and run semantic similarity search.",
    "network": "Distributed peer fabric: peer monitor, peer chat, agent relay.",
    "clubhouse": "Connected Clubhouse account and its live rooms.",
    "observability": "Inspect live client / inbound / outbound I/O data flow.",
}

# Keywords that auto-preload a group for a turn (so common asks stay one-shot). A
# group's own name is always an implicit keyword.
_GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "files": (
        "file",
        "directory",
        "folder",
        "read",
        "write",
        "create",
        "delete",
        "path",
        "ls",
    ),
    "editor": (
        "editor",
        "buffer",
        "code",
        "edit",
        "refactor",
        "format",
        "rename",
        "diagnostic",
    ),
    "terminal": (
        "terminal",
        "shell",
        "run",
        "command",
        "exec",
        "bash",
        "npm",
        "pip",
        "git",
    ),
    "visualizer": (
        "visualizer",
        "render",
        "pygame",
        "canvas",
        "three",
        "babylon",
        "draw",
        "animation",
    ),
    "vectordb": ("vector", "semantic", "embedding", "upsert", "similarity"),
    "network": ("peer", "node", "collab", "relay", "monitor"),
    "clubhouse": ("clubhouse", "room"),
}


def _group_of(name: str) -> str:
    """The group a tool belongs to: its namespace before the first dot. Layout verbs
    (no dot) belong to the always-present 'layout' core."""
    return name.split(".", 1)[0] if "." in name else "layout"


def _core_tools() -> list[dict[str, Any]]:
    """Always-present tools: the layout verbs, the peer tools, and the meta tools."""
    return list(LAYOUT_TOOLS) + list(PEER_TOOLS) + list(META_TOOLS)


def _all_dynamic_tools(conn: WsConnection) -> list[dict[str, Any]]:
    """Every browser-pushed tool, deduped against the static core (core wins)."""
    seen = {t["function"]["name"] for t in _core_tools()}
    out: list[dict[str, Any]] = []
    for t in _manifest_to_tools(getattr(conn, "agent_tools", [])):
        name = t["function"]["name"]
        if name in seen:
            continue
        out.append(t)
        seen.add(name)
    return out


def _group_catalog(conn: WsConnection) -> list[dict[str, Any]]:
    """The loadable groups (dynamic tools only), each with a description + count."""
    counts: dict[str, int] = {}
    for t in _all_dynamic_tools(conn):
        g = _group_of(t["function"]["name"])
        counts[g] = counts.get(g, 0) + 1
    return [
        {"name": g, "description": _GROUP_DESCRIPTIONS.get(g, f"{g} tools"), "tools": n}
        for g, n in sorted(counts.items())
    ]


def _preload_groups(
    conn: WsConnection, prompt: str = "", history: list[Any] | None = None
) -> set[str]:
    """Groups to activate up front from prompt/history keywords, so a typical request
    doesn't need an explicit load_tools round first."""
    text = prompt.lower()
    if history:
        for m in history:
            if isinstance(m, dict) and isinstance(m.get("content"), str):
                text += " " + m["content"].lower()
    active: set[str] = set()
    for g in {grp["name"] for grp in _group_catalog(conn)}:
        if g in text or any(kw in text for kw in _GROUP_KEYWORDS.get(g, ())):
            active.add(g)
    return active


def _select_tools(conn: WsConnection, active_groups: set[str]) -> list[dict[str, Any]]:
    """The tool list presented this round: core + every dynamic tool whose group is
    active. Capped at TOOL_BUDGET as a backstop (core is always kept first)."""
    selected = _core_tools()
    for t in _all_dynamic_tools(conn):
        if _group_of(t["function"]["name"]) in active_groups:
            selected.append(t)
    if len(selected) > TOOL_BUDGET:
        logger.warning(
            "tool list %d exceeds budget %d; truncating dynamic tools (active: %s)",
            len(selected),
            TOOL_BUDGET,
            sorted(active_groups),
        )
        selected = selected[:TOOL_BUDGET]
    return selected


def _tools_for(
    conn: WsConnection, prompt: str = "", history: list[Any] | None = None
) -> list[dict[str, Any]]:
    """The tools a turn STARTS with: core + the keyword-preloaded groups. The turn
    loop recomputes this per round as the model loads more groups (run_agent_turn)."""
    return _select_tools(conn, _preload_groups(conn, prompt, history))


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
        # Introspection for the chat widget's `/tools` command: the FULL catalog,
        # labeled by group. With progressive disclosure the model only *sees* the core
        # plus loaded groups per turn, but `/tools` shows everything that's reachable.
        catalog = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "source": _group_of(t["function"]["name"]),
            }
            for t in (_core_tools() + _all_dynamic_tools(conn))
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
    """The metadata entry for a tool, or None for layout/unknown tools. Static
    backend tools (e.g. agent.ask_peer) declare theirs in _STATIC_TOOL_META; frontend
    tools carry theirs in the pushed manifest."""
    if name in _STATIC_TOOL_META:
        return _STATIC_TOOL_META[name]
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
    # A remote (peer-driven) turn forces its own mode (network.remoteAgentMode) and
    # never has a human to prompt; a local turn uses the user's session mode.
    mode = getattr(conn, "force_mode", None) or permission_store.load_mode()
    rules = permission_store.load_rules()
    decision = permissions.evaluate(call.name, specifier, side_effect, mode, rules)
    if decision is permissions.Decision.ALLOW:
        return True
    if decision is permissions.Decision.DENY:
        return False
    # ASK: a remote turn has no human behind it — deny rather than block/prompt.
    if getattr(conn, "is_remote", False):
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


async def _run_backend_tool(conn: WsConnection, call: Any) -> Any:
    """Execute a backend-resolved tool (the peer fabric verbs) against the
    process-global PeerHub. Imported lazily to avoid an import cycle with the network
    module (whose agent_bridge imports this orchestrator)."""
    from backend.modules.network import agent_bridge
    from backend.modules.network.hub import peer_hub

    if call.name == "list_peers":
        return {"peers": [p.model_dump() for p in peer_hub.list_peers()]}
    if call.name == "agent.ask_peer":
        peer_id = str(call.arguments.get("peerId", ""))
        prompt = str(call.arguments.get("prompt", ""))
        if not peer_id or not prompt:
            return {"error": "agent.ask_peer needs peerId and prompt"}
        origin_chain = getattr(conn, "origin_chain", None)
        return await agent_bridge.ask_peer(peer_id, prompt, origin_chain=origin_chain)
    return {"error": f"unknown backend tool {call.name}"}


async def _dispatch_call(
    conn: WsConnection, turn_id: str, call: Any, active_groups: set[str]
) -> Any:
    """Resolve one tool call under progressive disclosure. The meta tools mutate
    `active_groups` (so the next round presents more tools); a known dynamic tool
    whose group isn't active yet is auto-loaded (forgiving); everything else is gated,
    then run in the backend or relayed to the browser."""
    name = call.name
    if name == "list_tool_groups":
        return {"groups": _group_catalog(conn), "loaded": sorted(active_groups)}
    if name == "load_tools":
        requested = call.arguments.get("groups")
        if isinstance(requested, str):
            requested = [requested]
        requested = requested if isinstance(requested, list) else []
        available = {g["name"] for g in _group_catalog(conn)}
        loaded = [g for g in requested if g in available]
        active_groups.update(loaded)
        tools_now = [
            t["function"]["name"]
            for t in _all_dynamic_tools(conn)
            if _group_of(t["function"]["name"]) in loaded
        ]
        unknown = [g for g in requested if g not in available]
        return {"loaded": loaded, "tools": tools_now, "unknown": unknown}

    # Forgiveness: the model called a known tool from a group it hadn't loaded — pull
    # the group in (so it stays visible next round) and run the call.
    if any(t["function"]["name"] == name for t in _all_dynamic_tools(conn)):
        active_groups.add(_group_of(name))

    if not await _gate(conn, turn_id, call):
        return {"error": "denied by permission policy"}
    if name in BACKEND_TOOL_NAMES:
        return await _run_backend_tool(conn, call)
    return await _call_frontend_tool(conn, turn_id, name, call.arguments)


async def run_agent_loop(
    conn: WsConnection,
    turn_id: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    info: Any,
    endpoint: str,
    model: str,
    emit: Callable[[str, str], Awaitable[None]],
    *,
    temperature: float,
    context_size: int | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    active_groups: set[str] | None = None,
) -> str:
    """The shared tool-calling loop: stream the provider, relay each gated tool call
    to the frontend, and repeat until the model returns a final answer (no tool
    calls) or `MAX_ROUNDS` is hit. Returns the final answer text; appends the
    assistant/tool messages to `messages` in place.

    Reused by the chat orchestrator (`run_agent_turn`) and the flow executor's Agent
    nodes — each caller assembles its own `messages`/`tools`, passes an `emit`
    callback (so reasoning/answer deltas go to the right channel + event shape), and
    a `turn_id` that correlates the relayed `tool_call`/`approval_request` round
    trips. Provider HTTP errors propagate to the caller.

    When `active_groups` is given, the loop runs **progressive disclosure**: the tool
    list is recomputed each round from the active groups (`_select_tools`) and calls
    route through `_dispatch_call` (meta tools + auto-load). Otherwise the fixed
    `tools` list is used with direct dispatch — the path flow Agent nodes use."""
    progressive = active_groups is not None
    forced_retry_used = False
    async with instrumented_client(timeout=120) as client:
        for _ in range(MAX_ROUNDS):
            # Under progressive disclosure, inject the groups loaded last round.
            if progressive:
                tools = _select_tools(conn, active_groups)
            result = await P.chat_stream(
                client,
                info,
                endpoint,
                model,
                messages,
                tools,
                emit,
                temperature=temperature,
                context_size=context_size,
                max_tokens=max_tokens,
                top_p=top_p,
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
                    emit,
                    temperature=temperature,
                    tool_choice="required",
                    context_size=context_size,
                    max_tokens=max_tokens,
                    top_p=top_p,
                )
                messages.append(result.assistant_message)

            if not result.tool_calls:
                return result.content
            for call in result.tool_calls:
                if progressive:
                    tool_result = await _dispatch_call(
                        conn, turn_id, call, active_groups
                    )
                elif not await _gate(conn, turn_id, call):
                    tool_result = {"error": "denied by permission policy"}
                elif call.name in BACKEND_TOOL_NAMES:
                    # Resolved in the backend (peer fabric), not relayed to the UI.
                    tool_result = await _run_backend_tool(conn, call)
                else:
                    tool_result = await _call_frontend_tool(
                        conn, turn_id, call.name, call.arguments
                    )
                messages.append(P.tool_result_message(info, call, tool_result))
    return "(stopped after too many steps)"


async def run_agent_turn(
    conn: WsConnection,
    turn_id: str,
    prompt: str,
    history: list[Any] | None = None,
    context: dict[str, Any] | None = None,
    *,
    remote: bool = False,
) -> None:
    """Drive one user turn: assemble the conversation, run the shared tool-calling
    loop, and send the authoritative answer. The provider dialect (Ollama vs
    OpenAI-compatible) is hidden behind providers. `history` carries prior
    user/assistant turns from the chat widget so the conversation is multi-turn
    while the backend stays stateless per turn. `context` carries the user's focused
    pane snapshot (currently the open editor buffer) so the model can act on what
    the user is looking at without a discovery round-trip.

    `remote=True` marks a turn driven by a *peer's* agent (no browser behind it): it
    runs with no actuating tools, so a remote agent answers from the model but cannot
    drive this machine. See modules/network agent_bridge."""
    config = _load_config()
    if config is None:
        await conn.send_json(
            _evt("error", {"turnId": turn_id, "message": "Agent not configured"})
        )
        return
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint
    model = _orchestrator_model(config.model)
    # A remote turn gets no tools (it can't reach a browser to execute them, and must
    # not act on this machine). A local turn presents the core plus whatever tool
    # groups are active — seeded from the prompt's keywords and grown as the model
    # calls load_tools; the list is recomputed each round (progressive disclosure).
    # Local turns use progressive disclosure (active_groups drives a per-round tool
    # list); a remote turn gets None → the loop runs with no tools (it has no browser
    # to execute them and must not act on this machine).
    active_groups: set[str] | None = (
        None if remote else _preload_groups(conn, prompt, history)
    )
    editor_msg = _active_editor_message(context)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_history_messages(history),
        # The focused buffer goes right before the user turn so it's the freshest
        # context the model sees (and isn't diluted by prior conversation).
        *([editor_msg] if editor_msg else []),
        {"role": "user", "content": prompt},
    ]

    async def emit(reasoning: str, content: str) -> None:
        # Relay the model's streamed reasoning + answer tokens to the chat widget as
        # they arrive (the final `answer` event below stays authoritative).
        if reasoning:
            await conn.send_json(
                _evt("reasoning", {"turnId": turn_id, "delta": reasoning})
            )
        if content:
            await conn.send_json(_evt("token", {"turnId": turn_id, "delta": content}))

    try:
        text = await run_agent_loop(
            conn,
            turn_id,
            messages,
            [],  # placeholder; progressive disclosure recomputes per round (local)
            info,
            endpoint,
            model,
            emit,
            temperature=_tool_temperature(),
            context_size=_tool_context_size(),
            max_tokens=_tool_max_tokens(),
            top_p=_tool_top_p(),
            active_groups=active_groups,
        )
        await conn.send_json(_evt("answer", {"turnId": turn_id, "text": text}))
        await conn.send_json(_evt("done", {"turnId": turn_id}))
    except httpx.HTTPError as exc:
        await conn.send_json(
            _evt(
                "error", {"turnId": turn_id, "message": f"{type(exc).__name__}: {exc}"}
            )
        )
