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
from backend.modules.telemetry.instrument import instrumented_client
from backend.modules.ws import WsConnection
from backend.sdk.types import AgentSpec

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


def _agent_setting(agent_id: str, key: str, default: Any = None) -> Any:
    """Per-agent settings resolution (lazy import — roster imports this module)."""
    from backend.modules.agent.roster import agent_setting

    return agent_setting(agent_id, key, default)


def _tool_temperature(agent_id: str = "main") -> float:
    """Sampling temperature for an agent's turns (settings-overridable, per-agent
    `agent.<id>.temperature` falling back to `agent.orchestrator.temperature`)."""
    value = _agent_setting(agent_id, "temperature", DEFAULT_TOOL_TEMPERATURE)
    try:
        return float(value)
    except (TypeError, ValueError):
        return DEFAULT_TOOL_TEMPERATURE


def _tool_context_size(agent_id: str = "main") -> int | None:
    """Context size limit (num_ctx) for an agent's turns (settings-overridable)."""
    value = _agent_setting(agent_id, "contextSize", None)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tool_max_tokens(agent_id: str = "main") -> int | None:
    """Max output tokens for an agent's turns (settings-overridable)."""
    value = _agent_setting(agent_id, "maxTokens", None)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tool_top_p(agent_id: str = "main") -> float | None:
    """Top P sampling for an agent's turns (settings-overridable)."""
    value = _agent_setting(agent_id, "topP", None)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _orchestrator_model(default: str, agent_id: str = "main") -> str:
    """Model for an agent's turns. A separate override (settings-overridable)
    lets a stronger model drive tool calls than the one used for chat/autosuggest
    — and each roster agent can pin its own (`agent.<id>.model`); blank falls
    back to the orchestrator override, then the configured agent model."""
    value = _agent_setting(agent_id, "model", "")
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
    "You are the orchestrator for horrible-dashboard, an app whose screen is a "
    "'frame': a center grid of AREAS (Blender-style splits), three tool DOCKS "
    "(left/right/bottom), and per-pane REGION strips. You arrange the screen by "
    "opening/closing panes, splitting/joining/resizing areas, toggling regions "
    "and docks, and managing workspaces, using the provided tools.\n"
    "Rules:\n"
    "- Views have a ROLE: 'document' views open as tabs in center areas, "
    "'widget' views take a center area of their own, and 'tool' views live only "
    "in the docks (use open_tool_in_dock for tools; open_pane routes any view "
    "correctly). FIRST call list_available_panes to find the view whose title "
    "matches, THEN open it by ID. Do NOT treat a view as a workspace.\n"
    "- To arrange the screen (split_area/join_area/resize_area/move_pane/"
    "fullscreen_area/float_pane), FIRST call get_layout (the whole frame: areas, "
    "docks, regions, floating) or list_open_panes to get live instanceIds. "
    "Geometry tools take an instanceId (or areaId from get_layout), NOT a view ID.\n"
    "- REGIONS are toggleable side strips inside a pane (e.g. the editor's "
    "Outline on its right strip). Use toggle_region {instanceId, position} and "
    "set_region_view {instanceId, viewId}; a view's regions are listed by "
    "list_available_panes.\n"
    "- If the user refers to a pane via a title, file name, or instance ID (e.g., "
    "'pane:main.py' or 'pane:editor.buffer#1'), match it against list_open_panes "
    "to find the correct instanceId to target.\n"
    "- If the user refers to a file via an absolute or relative path prefixed "
    "with '@' (e.g., '@absolute_path'), use that path directly with files/editor "
    "tools.\n"
    "- Only use list_workspaces / create_workspace / switch_workspace when the "
    "user explicitly talks about workspaces or tabs.\n"
    "- Ids are not guessable; always discover them with get_layout or a list_* "
    "tool first.\n"
    "- When the user asks ABOUT what's on screen, the layout, or a view's "
    "contents, call get_layout first, then get_pane_context on the relevant "
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


# App-level layout verbs. Generic over ids (no enums beyond directions/positions)
# — the model discovers valid ids through the read tools, keeping the catalog
# frontend-owned. Vocabulary: center AREAS host documents/widgets, DOCKS host
# tools, REGIONS are per-pane strips.
LAYOUT_TOOLS: list[dict[str, Any]] = [
    _tool(
        "list_available_panes",
        "List every view that can be opened, with id, title, role "
        "('document' = tabs in a center area, 'widget' = its own center area, "
        "'tool' = lives in a dock), its default dock (tools), and any region "
        "views it hosts. Call this to find a valid view ID before opening.",
    ),
    _tool("list_workspaces", "List the named workspaces and which one is active."),
    _tool(
        "list_open_panes",
        "List the panes currently open in the active workspace, with each pane's "
        "view ID, live instanceId, title, role, location (center area / dock / "
        "floating), and whether it exposes agent-readable context.",
    ),
    _tool(
        "get_layout",
        "Read the whole frame: the center split tree (areas with their document "
        "tabs and region strips), the three docks and their tools, floating "
        "panes, and which area is fullscreen/focused. The orientation read — "
        "call it before arranging anything.",
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
        "Open a view in the active workspace, routed by its role (documents tab "
        "into a center area, widgets take their own area, tools go to their "
        "dock). Some panes take params — e.g. the training notebook needs "
        "`params: {projectId, notebook}` to know which project to open.",
        {
            "id": {
                "type": "string",
                "description": "View ID from list_available_panes",
            },
            "params": {
                "type": "object",
                "description": "Optional pane parameters (e.g. {projectId, notebook}).",
            },
        },
        ["id"],
    ),
    _tool(
        "close_pane",
        "Close an open pane by its instanceId or view ID.",
        {
            "id": {
                "type": "string",
                "description": "Instance ID or view ID of the pane to close",
            }
        },
        ["id"],
    ),
    _tool(
        "focus_pane",
        "Bring an open pane forward (activate its tab / dock slot / floating "
        "card). instanceId from list_open_panes.",
        {"instanceId": {"type": "string"}},
        ["instanceId"],
    ),
    _tool(
        "split_area",
        "Split the center area holding a pane. 'left'/'right' put the new area "
        "beside it ('vertical' alias), 'above'/'below' stack it ('horizontal' "
        "alias). viewId is OPTIONAL — omit it to duplicate the area's own view "
        "into the new area; pass a view ID to put a different view there.",
        {
            "instanceId": {
                "type": "string",
                "description": "Pane instance (or areaId from get_layout) to split",
            },
            "direction": {
                "type": "string",
                "enum": ["vertical", "horizontal", "left", "right", "above", "below"],
                "description": "vertical=side by side, horizontal=stacked",
            },
            "viewId": {
                "type": "string",
                "description": "Optional view for the new area; omit to duplicate",
            },
        },
        ["instanceId", "direction"],
    ),
    _tool(
        "join_area",
        "Join the neighboring area in a direction into this one (the neighbor "
        "disappears; document tabs are adopted when both hold documents). Only "
        "aligned neighbors can join.",
        {
            "instanceId": {
                "type": "string",
                "description": "Pane instance (or areaId) that absorbs its neighbor",
            },
            "direction": {"type": "string", "enum": ["left", "right", "up", "down"]},
        },
        ["instanceId", "direction"],
    ),
    _tool(
        "resize_area",
        "Resize the center area holding a pane. Sizes are in pixels; pass width "
        "and/or height.",
        {
            "instanceId": {"type": "string"},
            "width": {"type": "number", "description": "Target width in pixels"},
            "height": {"type": "number", "description": "Target height in pixels"},
        },
        ["instanceId"],
    ),
    _tool(
        "move_pane",
        "Move a center pane into another area: pass areaId (from get_layout) for "
        "an exact target, or direction to move it to the neighboring area. "
        "Documents stack as tabs; widgets need an empty target area.",
        {
            "instanceId": {"type": "string", "description": "Pane to move"},
            "areaId": {
                "type": "string",
                "description": "Target area (from get_layout)",
            },
            "direction": {"type": "string", "enum": ["left", "right", "up", "down"]},
        },
        ["instanceId"],
    ),
    _tool(
        "fullscreen_area",
        "Temporarily expand the area holding a pane to fill the whole frame "
        "(on: true, the default), or restore the layout (on: false — instanceId "
        "optional when restoring).",
        {
            "instanceId": {"type": "string"},
            "on": {"type": "boolean", "description": "false restores the layout"},
        },
        [],
    ),
    _tool(
        "toggle_region",
        "Toggle a pane's region strip (the Blender-style side panel inside its "
        "area, e.g. the editor's Outline strip). Pass open to force a state.",
        {
            "instanceId": {"type": "string", "description": "Host pane instance"},
            "position": {"type": "string", "enum": ["left", "right", "bottom"]},
            "open": {"type": "boolean", "description": "Force open (true) or closed"},
        },
        ["instanceId", "position"],
    ),
    _tool(
        "set_region_view",
        "Open a specific region view on its host pane and make it the visible "
        "one in its strip (e.g. show git.provenance on an editor buffer). The "
        "host view's region views are listed by list_available_panes.",
        {
            "instanceId": {"type": "string", "description": "Host pane instance"},
            "viewId": {"type": "string", "description": "Region view to show"},
        },
        ["instanceId", "viewId"],
    ),
    _tool(
        "open_tool_in_dock",
        "Open (or focus) a role:'tool' view in a dock. Omit dock to use the "
        "tool's default side.",
        {
            "id": {"type": "string", "description": "Tool view ID"},
            "dock": {"type": "string", "enum": ["left", "right", "bottom"]},
        },
        ["id"],
    ),
    _tool(
        "toggle_dock",
        "Show or hide a dock (its tools stay loaded). Pass visible to force a state.",
        {
            "dock": {"type": "string", "enum": ["left", "right", "bottom"]},
            "visible": {"type": "boolean"},
        },
        ["dock"],
    ),
    _tool(
        "float_pane",
        "Pop an open pane out into a floating card over the center grid. "
        "instanceId from list_open_panes.",
        {"instanceId": {"type": "string"}},
        ["instanceId"],
    ),
    _tool(
        "dock_pane",
        "Put a floating pane back into the center grid. instanceId from "
        "list_open_panes.",
        {"instanceId": {"type": "string"}},
        ["instanceId"],
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

# Delegation to the local specialized-agent roster (coder/dba/researcher + plugin
# agents). Backend-resolved like the peer tools, but the sub-agent runs on THIS
# node against the same browser connection, so it can actuate (unlike ask_peer's
# tool-less remote turns). Only agents whose spec allows it see this tool.
DELEGATE_TOOLS: list[dict[str, Any]] = [
    _tool(
        "agent.delegate",
        "Delegate a task to one of this app's specialized agents (e.g. 'coder' for "
        "editor/code work, 'dba' for SQL/schema work, 'researcher' for web/library "
        "research) and get its answer back. The specialized agent runs here with "
        "its own scoped tools and can act on the app. Use for tasks squarely in a "
        "specialist's domain; do the work yourself when it spans domains.",
        {
            "agentId": {
                "type": "string",
                "description": "Specialized agent id, e.g. 'coder', 'dba', 'researcher'",
            },
            "prompt": {"type": "string", "description": "The task for the agent"},
        },
        ["agentId", "prompt"],
    ),
]

# Names dispatched in the backend (not relayed to the browser).
BACKEND_TOOL_NAMES = {t["function"]["name"] for t in PEER_TOOLS + DELEGATE_TOOLS}

# Side-effect metadata for static backend tools (the browser manifest carries this
# for frontend tools; static tools declare it here so the gate can see it).
# agent.ask_peer reaches another machine, so it's gated; list_peers is read-only.
# agent.delegate hands the wheel to a sub-agent (which is itself gated per call,
# under its own mode) — the prompt makes delegation itself a visible, gated step.
_STATIC_TOOL_META: dict[str, dict[str, Any]] = {
    "agent.ask_peer": {
        "name": "agent.ask_peer",
        "sideEffect": True,
        "specifierTemplate": "{peerId}",
    },
    "agent.delegate": {
        "name": "agent.delegate",
        "sideEffect": True,
        "specifierTemplate": "{agentId}",
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

# Cap kept only as a safety backstop now that groups load on demand. The flagship
# training flow legitimately loads both the `training` and `notebook` groups at once
# (core 18 + training 12 + notebook 9 = 39), so the backstop sits above that.
TOOL_BUDGET = 44

# Human-readable blurbs for known groups; unknown groups get a generic fallback.
_GROUP_DESCRIPTIONS: dict[str, str] = {
    "files": "Browse, read, search, create, and edit files in the workspace.",
    "editor": "Inspect and modify open editor buffers (read, propose edits, format, rename).",
    "terminal": "Run shell commands and manage terminal sessions.",
    "visualizer": "Render Canvas / Three.js / Babylon.js animations and stream Pygame frames.",
    "database": "Connect to and query SQL/vector databases (psql-like): list connections, inspect schema, run read queries, write/execute statements, and semantic search the app DB.",
    "network": "Distributed peer fabric: peer monitor, peer chat, agent relay.",
    "clubhouse": "Connected Clubhouse account and its live rooms.",
    "game": "Play the current game seat: read the observation, choose a legal action.",
    "observability": "Inspect live client / inbound / outbound I/O data flow.",
    "training": (
        "Build & train neural networks: search/create Kaggle/HF/Gym projects, "
        "per-project venvs, install deps, start/stop training runs, push to "
        "Kaggle kernels or Colab, render manim explainers."
    ),
    "notebook": "Read, edit, and execute cells of the open training notebook.",
    "symbols": (
        "Semantic + exact lookup over the symbol/docs index: installed package "
        "APIs (signatures, docstrings), database schemas, and this app's docs."
    ),
    "research": (
        "Deep-research runs (durable multi-agent investigations ending in cited "
        "reports), plus capturing pages and PDFs into the knowledge library."
    ),
    "arxiv": "Search arXiv, read abstracts, and download papers into the library.",
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
    # The connector groups. A group's own name is always an implicit keyword, so
    # "github" needs no entry — these are the words people actually use instead.
    "github": (
        "repo",
        "repository",
        "pull request",
        "issue",
        "commit",
        "branch",
        "readme",
    ),
    "google": (
        "drive",
        "google doc",
        "spreadsheet",
        "my documents",
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
    "database": (
        "database",
        "sql",
        "query",
        "table",
        "schema",
        "psql",
        "postgres",
        "sqlite",
        "select",
        "connection",
        "vector",
        "semantic",
        "embedding",
        "similarity",
    ),
    "network": ("peer", "node", "collab", "relay", "monitor"),
    "clubhouse": ("clubhouse", "room"),
    "training": (
        "train",
        "training",
        "kaggle",
        "dataset",
        "gym",
        "gymnasium",
        "colab",
        "huggingface",
        "competition",
        "neural",
        "pytorch",
        "torch",
        "manim",
        "venv",
        "model",
    ),
    "notebook": ("notebook", "cell", "kernel", "ipynb", "jupyter"),
    "symbols": (
        "docstring",
        "signature",
        "documentation",
        "api reference",
        "how do i use",
        "what does",
    ),
    "research": (
        "research",
        "deep research",
        "investigate",
        "report",
        "cite",
        "citation",
        "sources",
        "capture",
        "save page",
        "save pdf",
    ),
    "arxiv": ("arxiv", "paper", "preprint", "abstract", "publication"),
}


def _group_of(name: str) -> str:
    """The group a tool belongs to: its namespace before the first dot. Layout verbs
    (no dot) belong to the always-present 'layout' core."""
    return name.split(".", 1)[0] if "." in name else "layout"


def _core_tools(spec: AgentSpec | None = None) -> list[dict[str, Any]]:
    """Always-present tools: the layout verbs, the peer/delegate tools (spec-gated),
    the meta tools, and the *ungrouped* backend-plugin agent tools (registered via
    backend.sdk). Plugin tools that declare a `group` are disclosed progressively
    instead (see `_all_dynamic_tools`). `spec=None` (the main orchestrator and
    every pre-roster caller) keeps the full core."""
    from backend.sdk.registry import registry as _plugins

    tools = list(LAYOUT_TOOLS)
    if spec is None or spec.include_peer_tools:
        tools += list(PEER_TOOLS)
    if spec is None or spec.can_delegate:
        tools += list(DELEGATE_TOOLS)
    tools += list(META_TOOLS)
    core_plugin = _plugins.provider_tools(grouped=False)
    if spec is not None and spec.tool_groups is not None:
        allowed = set(spec.tool_groups)
        core_plugin = [
            t for t in core_plugin if _group_of(t["function"]["name"]) in allowed
        ]
    return tools + core_plugin


def _all_dynamic_tools(conn: WsConnection) -> list[dict[str, Any]]:
    """Every progressively-disclosed tool — grouped backend-plugin tools plus every
    browser-pushed tool — deduped against the static core (core wins)."""
    from backend.sdk.registry import registry as _plugins

    seen = {t["function"]["name"] for t in _core_tools()}
    out: list[dict[str, Any]] = []
    candidates = _plugins.provider_tools(grouped=True) + _manifest_to_tools(
        getattr(conn, "agent_tools", [])
    )
    for t in candidates:
        name = t["function"]["name"]
        if name in seen:
            continue
        out.append(t)
        seen.add(name)
    return out


def _group_description(group: str) -> str:
    """A group's one-line blurb for the catalog.

    Connectors describe themselves once (`Connector.blurb`) and that single definition
    feeds both the home page's tile and this catalog — a connector's id is also its
    tool namespace, so the lookup is direct."""
    if known := _GROUP_DESCRIPTIONS.get(group):
        return known
    from backend.sdk.registry import registry as _plugins

    if connector := _plugins.connectors.get(group):
        return connector.blurb
    # MCP servers describe themselves; their groups are named `mcp-<id>`.
    from backend.modules.mcp import bridge as _mcp

    if described := _mcp.group_description(group):
        return described
    return f"{group} tools"


def _group_guide(group: str) -> str | None:
    """A group's full usage guide — the second tier of disclosure.

    The blurb (above) answers "would this group help?"; the guide answers "how do I use
    it without getting it wrong?" — search-qualifier syntax, useless argument
    combinations, provider quirks. It's only ever delivered once a group is active, so
    it costs nothing on turns that don't touch it."""
    from backend.sdk.registry import registry as _plugins

    if connector := _plugins.connectors.get(group):
        return connector.resolve_guide()
    # An MCP server's `instructions` + prompt/resource catalog, assembled by the bridge.
    from backend.modules.mcp import bridge as _mcp

    return _mcp.group_guide(group)


def _guides_text(groups: set[str]) -> str | None:
    """The guides for `groups`, concatenated, or None if none of them have one."""
    parts = [text for g in sorted(groups) if (text := _group_guide(g))]
    return "\n\n---\n\n".join(parts) if parts else None


def _guides_message(groups: set[str]) -> dict[str, Any] | None:
    """A system message carrying the guides for the groups a turn starts with.

    Needed because a keyword preload activates a group *without* the model ever calling
    `load_tools` — which is the common case ("search my github repos" preloads `github`
    outright). Without this the guide would almost never reach the model."""
    text = _guides_text(groups)
    return {"role": "system", "content": text} if text else None


def _group_catalog(
    conn: WsConnection, allowed: set[str] | None = None
) -> list[dict[str, Any]]:
    """The loadable groups (dynamic tools only), each with a description + count.
    `allowed` restricts the catalog to a specialized agent's tool groups."""
    counts: dict[str, int] = {}
    for t in _all_dynamic_tools(conn):
        g = _group_of(t["function"]["name"])
        if allowed is not None and g not in allowed:
            continue
        counts[g] = counts.get(g, 0) + 1
    return [
        {"name": g, "description": _group_description(g), "tools": n}
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


def _select_tools(
    conn: WsConnection,
    active_groups: set[str],
    spec: AgentSpec | None = None,
    stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The tool list presented this round: core (spec-gated) + every dynamic tool
    whose group is active — and, for a scoped agent, allowed. Capped at
    TOOL_BUDGET as a backstop (core is always kept first).

    `stats`, when given, is filled with the pre-cap count so a caller can tell that
    truncation happened. The interpretability pane uses it to report dropped tools;
    without it the cap is invisible to everything but the log."""
    allowed = set(spec.tool_groups) if spec and spec.tool_groups is not None else None
    selected = _core_tools(spec)
    for t in _all_dynamic_tools(conn):
        group = _group_of(t["function"]["name"])
        if group in active_groups and (allowed is None or group in allowed):
            selected.append(t)
    if stats is not None:
        stats["selected"] = len(selected)
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
                agent_id=str(data.get("agentId") or "main"),
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
    from backend.sdk.registry import registry as _plugins

    plugin_tool = _plugins.agent_tools.get(name)
    if plugin_tool is not None:
        return plugin_tool.meta()
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


async def _gate(
    conn: WsConnection,
    turn_id: str,
    call: Any,
    mode_override: permissions.Mode | None = None,
) -> bool:
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
    # never has a human to prompt; a roster agent may carry its own default mode
    # (`agent.<id>.permissionMode` / spec.default_mode); otherwise the user's
    # session mode applies.
    mode = (
        getattr(conn, "force_mode", None)
        or mode_override
        or permission_store.load_mode()
    )
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


async def _run_backend_tool(conn: WsConnection, turn_id: str, call: Any) -> Any:
    """Execute a backend-resolved tool: the peer fabric verbs (against the
    process-global PeerHub) and local roster delegation. Imported lazily to avoid
    an import cycle with the network module (whose agent_bridge imports this
    orchestrator) and the delegate module (which reuses the loop here)."""
    if call.name == "agent.delegate":
        from backend.modules.agent.delegate import run_delegate

        return await run_delegate(
            conn,
            turn_id,
            str(call.arguments.get("agentId", "")),
            str(call.arguments.get("prompt", "")),
        )
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
        # Record the handoff off-node so the interpretability tree has no silent
        # gap. Opaque by nature: the peer assembles its context on its own machine.
        await _capture_peer_ask(
            conn, parent_turn_id=turn_id, peer_id=peer_id, prompt=prompt
        )
        return await agent_bridge.ask_peer(peer_id, prompt, origin_chain=origin_chain)
    return {"error": f"unknown backend tool {call.name}"}


async def _dispatch_call(
    conn: WsConnection,
    turn_id: str,
    call: Any,
    active_groups: set[str],
    spec: AgentSpec | None = None,
    mode_override: permissions.Mode | None = None,
) -> Any:
    """Resolve one tool call under progressive disclosure. The meta tools mutate
    `active_groups` (so the next round presents more tools); a known dynamic tool
    whose group isn't active yet is auto-loaded (forgiving); everything else is gated,
    then run in the backend or relayed to the browser. A scoped agent's `spec`
    restricts the catalog/auto-load to its allowed groups."""
    allowed = set(spec.tool_groups) if spec and spec.tool_groups is not None else None
    name = call.name
    if name == "list_tool_groups":
        return {
            "groups": _group_catalog(conn, allowed),
            "loaded": sorted(active_groups),
        }
    if name == "load_tools":
        requested = call.arguments.get("groups")
        if isinstance(requested, str):
            requested = [requested]
        requested = requested if isinstance(requested, list) else []
        available = {g["name"] for g in _group_catalog(conn, allowed)}
        loaded = [g for g in requested if g in available]
        active_groups.update(loaded)
        tools_now = [
            t["function"]["name"]
            for t in _all_dynamic_tools(conn)
            if _group_of(t["function"]["name"]) in loaded
        ]
        unknown = [g for g in requested if g not in available]
        result: dict[str, Any] = {
            "loaded": loaded,
            "tools": tools_now,
            "unknown": unknown,
        }
        # A tool result *is* a message, so returning the guide here is all it takes to
        # put it in context — no plumbing in the turn loop.
        if guide := _guides_text(set(loaded)):
            result["guide"] = guide
        return result

    # Forgiveness: the model called a known tool from a group it hadn't loaded — pull
    # the group in (so it stays visible next round) and run the call. A scoped
    # agent gets no forgiveness outside its allowed groups: the call is refused.
    if any(t["function"]["name"] == name for t in _all_dynamic_tools(conn)):
        group = _group_of(name)
        if allowed is not None and group not in allowed:
            return {"error": f"tool {name} is outside this agent's allowed groups"}
        active_groups.add(group)

    if not await _gate(conn, turn_id, call, mode_override):
        return {"error": "denied by permission policy"}
    from backend.sdk.registry import registry as _plugins

    if name in _plugins.agent_tools:
        return await _plugins.invoke_agent_tool(name, call.arguments)
    if name in BACKEND_TOOL_NAMES:
        return await _run_backend_tool(conn, turn_id, call)
    return await _call_frontend_tool(conn, turn_id, name, call.arguments)


async def _capture_context(conn: WsConnection, **fields: Any) -> None:
    """Hand one round's assembled context to the interpretability recorder.

    Imported lazily because the recorder imports `_group_of` from this module to
    label tools by group — a module-level import would close that cycle. Wrapped
    besides: interpretability is an observer, and an observer that can break the
    thing it observes is a bug, not a feature."""
    try:
        from backend.modules.interpretability import recorder

        await recorder.capture_round(
            conn, tool_budget=TOOL_BUDGET, tokenizer_repo=_tokenizer_repo(), **fields
        )
    except Exception:
        logger.debug("interpretability capture skipped", exc_info=True)


async def _capture_peer_ask(conn: WsConnection, **fields: Any) -> None:
    """Record an `agent.ask_peer` handoff for the interpretability tree. Same lazy
    import and same swallow-everything contract as `_capture_context`."""
    try:
        from backend.modules.interpretability import recorder

        await recorder.capture_peer_ask(conn, **fields)
    except Exception:
        logger.debug("interpretability peer capture skipped", exc_info=True)


def _tokenizer_repo() -> str:
    """`interpretability.modelRepo` — the HF repo that describes the loaded model.

    One setting drives both halves of the pane: `tokenizer.json` for exact token
    counts and `config.json` for the architecture diagram. The older
    `tokenizerRepo` key is still honoured so an existing override keeps working."""
    from backend.modules.settings.routes import _read

    values = _read()
    for key in ("interpretability.modelRepo", "interpretability.tokenizerRepo"):
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


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
    spec: AgentSpec | None = None,
    mode_override: permissions.Mode | None = None,
    parent_turn_id: str | None = None,
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
    agent_id = spec.id if spec else "main"
    async with instrumented_client(timeout=120) as client:
        for round_no in range(MAX_ROUNDS):
            # Under progressive disclosure, inject the groups loaded last round.
            tool_stats: dict[str, Any] = {}
            if progressive:
                tools = _select_tools(conn, active_groups, spec, tool_stats)
            # Snapshot the exact context this round before it goes out, for the
            # interpretability pane. Read-only and self-swallowing — a failed
            # capture must never cost the user their turn (see recorder.py).
            await _capture_context(
                conn,
                turn_id=turn_id,
                agent_id=agent_id,
                model=model,
                provider=str(getattr(info, "kind", "")),
                messages=messages,
                tools=tools,
                round_no=round_no,
                tools_selected=int(tool_stats.get("selected", len(tools))),
                active_groups=active_groups,
                context_size=context_size,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                parent_turn_id=parent_turn_id,
                agent_name=spec.name if spec else "",
                tool_groups=spec.tool_groups if spec else None,
                permission_mode=mode_override.value if mode_override else None,
            )
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
                        conn, turn_id, call, active_groups, spec, mode_override
                    )
                elif not await _gate(conn, turn_id, call, mode_override):
                    tool_result = {"error": "denied by permission policy"}
                elif call.name in BACKEND_TOOL_NAMES:
                    # Resolved in the backend (peer fabric), not relayed to the UI.
                    tool_result = await _run_backend_tool(conn, turn_id, call)
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
    agent_id: str = "main",
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
    drive this machine. See modules/network agent_bridge.

    `agent_id` selects the roster persona driving the turn ("main" is the
    unrestricted orchestrator): its system prompt, tool-group scope, per-agent
    model/hyperparameters, and default permission mode. Every event this turn
    emits is tagged with the agentId so the UI can attribute it."""
    from backend.modules.agent import roster

    spec = roster.get_agent(agent_id)
    if spec is None:
        await conn.send_json(
            _evt(
                "error",
                {
                    "turnId": turn_id,
                    "agentId": agent_id,
                    "message": f"Unknown agent '{agent_id}'",
                },
            )
        )
        return
    config = _load_config()
    if config is None:
        await conn.send_json(
            _evt(
                "error",
                {
                    "turnId": turn_id,
                    "agentId": agent_id,
                    "message": "Agent not configured",
                },
            )
        )
        return
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint
    model = _orchestrator_model(config.model, agent_id)
    # A remote turn gets no tools (it can't reach a browser to execute them, and must
    # not act on this machine) → active_groups=None → the loop runs tool-less.
    # A local main turn seeds its groups from prompt/history keywords and grows them
    # as the model calls load_tools (progressive disclosure, recomputed per round).
    # A specialized agent skips keyword preloading — its scope is declared: it
    # starts with spec.preload_groups and can only load within spec.tool_groups.
    active_groups: set[str] | None
    if remote:
        active_groups = None
    elif spec.tool_groups is None:
        active_groups = _preload_groups(conn, prompt, history)
    else:
        active_groups = set(spec.preload_groups)
    mode_override = None if remote else roster.resolve_mode(spec)
    editor_msg = _active_editor_message(context)
    # Guides for groups the turn starts with — the model never calls load_tools for
    # those, so this is the only chance to hand it the usage notes.
    guides_msg = _guides_message(active_groups) if active_groups else None
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": spec.system_prompt},
        *([guides_msg] if guides_msg else []),
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
                _evt(
                    "reasoning",
                    {"turnId": turn_id, "agentId": agent_id, "delta": reasoning},
                )
            )
        if content:
            await conn.send_json(
                _evt(
                    "token", {"turnId": turn_id, "agentId": agent_id, "delta": content}
                )
            )

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
            temperature=_tool_temperature(agent_id),
            context_size=_tool_context_size(agent_id),
            max_tokens=_tool_max_tokens(agent_id),
            top_p=_tool_top_p(agent_id),
            active_groups=active_groups,
            spec=spec,
            mode_override=mode_override,
        )
        await conn.send_json(
            _evt("answer", {"turnId": turn_id, "agentId": agent_id, "text": text})
        )
        await conn.send_json(_evt("done", {"turnId": turn_id, "agentId": agent_id}))
    except httpx.HTTPError as exc:
        await conn.send_json(
            _evt(
                "error",
                {
                    "turnId": turn_id,
                    "agentId": agent_id,
                    "message": f"{type(exc).__name__}: {exc}",
                },
            )
        )
