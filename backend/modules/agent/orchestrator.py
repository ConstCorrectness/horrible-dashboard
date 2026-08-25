"""Agent orchestrator: a backend-resident tool-calling loop that drives the UI.

The turn runs over the shared `/ws` socket (bidirectional, per-browser) so the
model can call tools that execute in the *frontend* and feed results back without
any HTTP-request↔connection correlation. The model is the user's configured local
provider (Ollama, LM Studio, or vLLM — see providers.py), called with `tools`;
the calls relayed to the browser are app-level **layout** verbs in this first
slice. See docs/modules/agent-chat.md.
"""

import asyncio
import json
import logging
import re
import time
import uuid
import weakref
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from backend.modules.agent import permission_store, permissions
from backend.modules.agent import providers as P
from backend.modules.agent.routes import _load_config
from backend.modules.telemetry import turn as telemetry_turn
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

# A weak model sometimes describes an action in prose without emitting the call, so
# it gets ONE nudged retry when the text reads like an unemitted call — action
# phrasing or a named tool. Runs on every dialect; OpenAI additionally gets
# `tool_choice="required"`, which Ollama has no equivalent for.
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


# Deliberately short. This rides on every round, and the geometry rules it used to
# carry (instanceIds vs view ids, split-vs-move, the region vocabulary) now live in
# the `layout` group's guide — delivered only once the arrangement verbs are actually
# loaded, so a turn that just reads a file no longer pays for them. `show` replaced
# the discover-then-open dance those rules existed to steer.
SYSTEM_PROMPT = (
    "You are the orchestrator for horrible-dashboard. The user's screen is a 'frame' "
    "of panes; you can show panes, read what they contain, and use tools.\n"
    "Rules:\n"
    "- To put something on screen — 'show/open/go to X' — call show with whatever the "
    "user called it (a pane title, a section or side-strip name, a workspace name). It "
    "opens or focuses it and returns its contents in one step. Do NOT call "
    "list_available_panes + open_pane + get_pane_context to do this.\n"
    "- A pane may have SECTIONS: tabs inside one pane (People has Friends, Messages, "
    "Discover). Name the section, not the pane — show('friends') lands on that tab. "
    "get_pane_context takes an optional section to read a different one.\n"
    "- When the user asks ABOUT what is on screen, answer from the workspace snapshot "
    "you were given; use show or get_pane_context to read a pane it does not cover. "
    "Do not guess.\n"
    "- To REARRANGE the screen (split, move, resize, dock, fullscreen, workspaces), "
    "first load_tools(['layout']) — those verbs are not loaded by default — and follow "
    "the guide it returns.\n"
    "- If the user refers to a file via a path prefixed with '@' (e.g. "
    "'@absolute_path'), use that path directly with files/editor tools.\n"
    "- To change code in an open editor buffer (format, rewrite, fix), use "
    "editor.proposeEdit (NOT editor.applyEdit) so the user reviews the diff and "
    "accepts or declines it.\n"
    "- Tools are organized into GROUPS, and you only see the ones loaded so far. If a "
    "task needs a capability you cannot see, call list_tool_groups, then "
    "load_tools([...]) to enable it before using its tools.\n"
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
        "show",
        "Put something in front of the user and return what it contains. Give it "
        "whatever the user called the thing — a pane title ('Friends', 'Terminal'), "
        "a view id, a side-strip name ('Outline'), or a workspace name. It opens it, "
        "or just focuses it if it is already on screen, and returns that pane's "
        "current contents, so you do NOT need list_available_panes, open_pane or "
        "get_pane_context to reach something. Use this for any 'show/open/go to X' "
        "request; the arrangement verbs are only for moving panes around.",
        {
            "target": {
                "type": "string",
                "description": "What to show, in the user's own words.",
            },
            "where": {
                "type": "string",
                "enum": ["here", "beside", "dock"],
                "description": "Optional placement hint. Omit for the default.",
            },
        },
        ["target"],
    ),
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
        {
            "instanceId": {"type": "string", "description": "Active pane instanceId"},
            "section": {
                "type": "string",
                "description": "Optional: read this section of a multi-section pane (switches to it)",
            },
        },
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
        "Documents stack as tabs by default; pass edge to split the target area "
        "instead and drop the pane into the new half, which is how you place a "
        "pane beside another one that is already open.",
        {
            "instanceId": {"type": "string", "description": "Pane to move"},
            "areaId": {
                "type": "string",
                "description": "Target area (from get_layout)",
            },
            "direction": {"type": "string", "enum": ["left", "right", "up", "down"]},
            "edge": {
                "type": "string",
                "enum": ["left", "right", "above", "below"],
                "description": (
                    "Split the target area toward this edge and put the pane in "
                    "the new half, rather than tabbing it in. Same vocabulary as "
                    "split_area."
                ),
            },
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
        "open_window",
        "Pop an open pane out into a free-floating desktop window. instanceId "
        "from list_open_panes. Optionally place it: snap puts it in a screen "
        "region, or rect gives exact pixels. Windows work on both a floating and "
        "a tiling desktop — on a tiling one a window is the escape hatch for a "
        "pane that should not participate in the tiling.",
        {
            "instanceId": {"type": "string"},
            "snap": {
                "type": "string",
                "enum": [
                    "left",
                    "right",
                    "top",
                    "bottom",
                    "tl",
                    "tr",
                    "bl",
                    "br",
                    "max",
                ],
                "description": "Screen region to snap to. `top` maximizes.",
            },
            "rect": {
                "type": "object",
                "description": "Exact pixel rect. Ignored when snap is given.",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "w": {"type": "number"},
                    "h": {"type": "number"},
                },
            },
        },
        ["instanceId"],
    ),
    _tool(
        "dock_window",
        "Put a windowed pane back into the tiling frame (a center area, or its "
        "dock for a tool pane). instanceId from list_open_panes.",
        {"instanceId": {"type": "string"}},
        ["instanceId"],
    ),
    _tool(
        "window_state",
        "Minimize, maximize, restore or snap a window, or send it to another "
        "desktop. One verb rather than five, so the whole vocabulary costs one "
        "schema. A minimized window keeps running — it is hidden, not closed.",
        {
            "instanceId": {"type": "string", "description": "A pane in the window"},
            "state": {
                "type": "string",
                "enum": ["minimize", "maximize", "restore", "snap", "move_to_desktop"],
            },
            "snap": {
                "type": "string",
                "enum": [
                    "left",
                    "right",
                    "top",
                    "bottom",
                    "tl",
                    "tr",
                    "bl",
                    "br",
                    "max",
                ],
                "description": "Required when state is `snap`.",
            },
            "workspaceId": {
                "type": "string",
                "description": "Required when state is `move_to_desktop`.",
            },
        },
        ["instanceId", "state"],
    ),
    _tool(
        "arrange_windows",
        "Lay every open window out at once. Minimized windows are left alone.",
        {
            "style": {
                "type": "string",
                "enum": ["grid", "cascade", "columns", "rows"],
            }
        },
        ["style"],
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

#: Appearance verbs, in their own lazily-loaded `desktop` group.
#:
#: Deliberately NOT in `layout`. Restyling the desktop is a rare request, and the
#: always-on core was cut 34 -> 11 tools for a measured reason (see
#: docs/modules/agent-chat.mdx): anything that lives in the core is paid for on
#: every turn by every agent, including the ones that never touch it. These load
#: on demand, or when the prompt mentions a theme or a wallpaper.
DESKTOP_TOOLS: list[dict[str, Any]] = [
    _tool(
        "desktop.set_backdrop",
        "Set what the active desktop shows behind its windows. Ids come from "
        "get_layout (`desktop.backdrops`). `image` takes params {url, fit, dim}; "
        "the url must be one returned by the wallpaper routes.",
        {
            "id": {"type": "string", "description": "A registered backdrop id"},
            "params": {
                "type": "object",
                "description": "Provider-specific options. Replaces the old ones.",
            },
        },
        ["id"],
    ),
    _tool(
        "desktop.set_theme",
        "Switch the app theme. Ids from get_layout (`desktop.themes`).",
        {"id": {"type": "string"}},
        ["id"],
    ),
    _tool(
        "desktop.set_mode",
        "Switch the active desktop between the tiling frame and free-floating "
        "windows. Every open pane survives either way, but split ratios do not "
        "round-trip exactly — say so if the user is likely to care.",
        {"mode": {"type": "string", "enum": ["tiling", "floating"]}},
        ["mode"],
    ),
    _tool(
        "desktop.configure_taskbar",
        "Reconfigure the taskbar. Omitted fields are left alone.",
        {
            "position": {"type": "string", "enum": ["bottom", "top"]},
            "zones": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["start", "windows", "spacer", "desktops", "tray", "clock"],
                },
                "description": "Zones in render order. An empty list is a bare strip.",
            },
            "showLabels": {"type": "boolean"},
            "autoHide": {"type": "boolean"},
        },
    ),
]

# The layout verbs split by what they cost. The five READ verbs are orientation —
# cheap, and any agent may need to see what the user is looking at, so they stay in
# every agent's core. The sixteen ARRANGEMENT verbs are the expensive half: a scoped
# agent that doesn't list "layout" in its tool_groups gets them only on demand, via
# load_tools("layout"). The main orchestrator keeps all 21 unconditionally — its job
# *is* driving the shell. This is the single biggest lever on tool-schema tokens:
# an agent that never rearranges panes was paying ~16 schemas every round.
LAYOUT_READ_TOOL_NAMES = frozenset(
    {
        # `show` is a write in the sense that it opens panes, but it belongs in the
        # cheap always-on set: it is the *only* way an agent without the arrangement
        # verbs can reach a surface at all, and it replaces three of these reads for
        # the common "show me X" turn.
        "show",
        "list_available_panes",
        "list_workspaces",
        "list_open_panes",
        "get_layout",
        "get_pane_context",
    }
)
LAYOUT_READ_TOOLS: list[dict[str, Any]] = [
    t for t in LAYOUT_TOOLS if t["function"]["name"] in LAYOUT_READ_TOOL_NAMES
]
LAYOUT_WRITE_TOOLS: list[dict[str, Any]] = [
    t for t in LAYOUT_TOOLS if t["function"]["name"] not in LAYOUT_READ_TOOL_NAMES
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

# Present only when the user actually has an enabled skill — see `_skill_tools`.
# Unconditionally in core it would be ~60 tokens on every turn of every install, which
# is the exact regression that took the core tool list from 34 down to 11.
SKILL_TOOLS: list[dict[str, Any]] = [
    _tool(
        "use_skill",
        "Read a skill: the user's own reusable instructions for a kind of task. Pass "
        "the name from the 'Available skills' list. The description you were shown is "
        "a summary — call this to get the actual instructions BEFORE starting the "
        "work, not after.",
        {
            "name": {
                "type": "string",
                "description": "The skill's name, e.g. 'new-module'.",
            }
        },
        ["name"],
    ),
]

META_TOOL_NAMES = {t["function"]["name"] for t in META_TOOLS} | {
    t["function"]["name"] for t in SKILL_TOOLS
}


def _skill_tools() -> list[dict[str, Any]]:
    """`use_skill`, but only when there is a skill to use.

    A user with no skills pays nothing for the feature: no catalog message and no tool
    schema. The check is cached (see `skills.agent.has_active`) because it runs on
    every round and would otherwise stat two directories per turn.
    """
    try:
        from backend.modules.skills import agent as skills_agent

        return list(SKILL_TOOLS) if skills_agent.has_active() else []
    except Exception:  # noqa: BLE001 - skills must never break a turn
        logger.debug("skills unavailable for this turn", exc_info=True)
        return []


# Cap kept only as a safety backstop now that groups load on demand.
#
# 38, not 44. Small local models stop reasoning at 40+ tool definitions (see
# docs/modules/agent-chat.mdx), so a backstop above that ceiling protected nothing —
# it let a turn through in exactly the state the ceiling warns about. 44 existed
# because the flagship training flow needed `training` (12) + `notebook` (9) on top of
# an 18-tool core; with the arrangement verbs out of core that same flow is now
# 11 + 12 + 9 = 32, so the cap can sit under the cliff without truncating real work.
TOOL_BUDGET = 38

# Human-readable blurbs for known groups; unknown groups get a generic fallback.
_GROUP_DESCRIPTIONS: dict[str, str] = {
    "evals": (
        "Measure how well a model uses this app's tools: list evaluation suites, "
        "start a sweep of a suite against a model, and read which cases failed and "
        "why. Read-mostly — it does not author cases."
    ),
    "layout": (
        "Arrange the app shell: open/close/focus panes, split and resize areas, "
        "move panes between docks and regions, create and switch workspaces. "
        "(Reading the layout needs no load — those verbs are always present.)"
    ),
    "desktop": (
        "How the desktop looks: its backdrop or wallpaper, the app theme, the "
        "taskbar's zones, and whether the desktop tiles or floats its windows."
    ),
    "lens": (
        "Read a recorded forward pass of a local model as words: what it was "
        "disposed to say at each layer and prompt position, how one vocabulary "
        "token's rank climbs through the layers, and what changing a prompt token "
        "does to all of it. Needs a trace — `llamacpp.trace` records one."
    ),
    "files": "Browse, read, search, create, and edit files in the workspace.",
    "editor": "Inspect and modify open editor buffers (read, propose edits, format, rename).",
    "terminal": "Run shell commands and manage terminal sessions.",
    "visualizer": "Render Canvas / Three.js / Babylon.js animations and stream Pygame frames.",
    "database": "Connect to and query SQL/vector databases (psql-like): list connections, inspect schema, run read queries, write/execute statements, and semantic search the app DB.",
    "social": (
        "The user's friends: list the roster with presence, message a person, or ask "
        "a friend's own agent a question. People are named by @handle, friend code, "
        "or display name."
    ),
    "mobile": "The user's paired phone: capture a photo, send it a notification.",
    "watch": (
        "Standing watches on people: tell the user when a friend comes online or "
        "goes offline. Survives the turn — use it for 'let me know when X logs in'."
    ),
    "notify": (
        "Notification rules: mute or unmute by category, by person, or everyone "
        "except one person, optionally for a set time; and report what is muted."
    ),
    "hassault": (
        "HorribleAssault matches: list maps and running matches, host one, invite a "
        "friend, add/remove bots, and read a match's state and surroundings."
    ),
    "browser": (
        "The embedded web browser: open a URL, read a page's content and "
        "accessibility snapshot, scrape, click, type, and save pages or media into a "
        "knowledge library."
    ),
    "library": (
        "Personal knowledge libraries: semantic-search a library, list its sources, "
        "and add new ones."
    ),
    "model": (
        "Neural-network designs in the interpretability pane: list and read saved "
        "designs, fork the model currently being inspected into an editable one, "
        "retune its hyperparameters and see what they cost in parameters, and get "
        "the PyTorch nn.Module it generates. Counts are estimates, not measurements."
    ),
    "keymap": "Inspect and rebind keyboard shortcuts.",
    "code": "Search the code symbol index (jumping the editor to a hit) and rebuild it.",
    "clubhouse": "Connected Clubhouse account and its live rooms.",
    "game": "Play the current game seat: read the observation, choose a legal action.",
    "observability": "Inspect live client / inbound / outbound I/O data flow.",
    "training": (
        "Build & train neural networks: search/create Kaggle/HF/Gym projects, "
        "per-project venvs, install deps, start/stop training runs, push to "
        "Kaggle kernels or Colab, render manim explainers — and read, edit, and "
        "execute the cells of the open TRAINING notebook (addressed by projectId)."
    ),
    # These two are different notebooks, and the blurbs are what the model picks a
    # group by. The training notebook's cell tools used to sit in this group under
    # `notebook.*` names, which is what the old wording described; they are
    # `training.*` now.
    "notebook": (
        "Read, edit, and execute cells of the open REACTIVE notebook (addressed by "
        "file path), and set its execution mode."
    ),
    "symbols": (
        "Semantic + exact lookup over the symbol/docs index: installed package "
        "APIs (signatures, docstrings), database schemas, and this app's docs."
    ),
    "research": (
        "Deep-research runs (durable multi-agent investigations ending in cited "
        "reports), plus capturing pages and PDFs into the knowledge library."
    ),
    "arxiv": "Search arXiv, read abstracts, and download papers into the library.",
    "localtrack": (
        "Track machine learning experiments locally: inspect projects, list runs, "
        "query loss/accuracy metric series with server-side downsampling, and view hyperparameters."
    ),
    "records": (
        "The user's own record tables (papers to read, contacts, intake forms, any "
        "row-shaped data): read them, propose field values for review, and define "
        "new tables."
    ),
}

# Keywords that auto-preload a group for a turn (so common asks stay one-shot). A
# group's own name is always an implicit keyword.
_GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Deliberately narrow: these must be words that only mean *arranging the shell*.
    # "open" and "close" are not here — they'd preload the arrangement verbs on
    # nearly every turn, which is exactly the cost this split exists to avoid.
    "desktop": (
        "theme",
        "wallpaper",
        "backdrop",
        "taskbar",
        "desktop",
        "dark mode",
        "light mode",
        "tiling",
    ),
    "layout": (
        "layout",
        "pane",
        "workspace",
        "dock",
        "split",
        "fullscreen",
        "tab strip",
        "rearrange",
    ),
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
    # Deliberately excludes "find", "read", "docs" and "documentation": those already
    # belong to `files`/`symbols`, and an overlapping keyword preloads both groups,
    # spending the tool budget twice for one intent.
    "search": (
        "web",
        "google it",
        "look up",
        "online",
        "latest",
        "current",
        "news",
        "recent",
        "browse the web",
        "who is",
        "url",
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
    # No `network` entry: there are no `network.*` tools, so the group never appears
    # in `_group_catalog` and any keywords here could never fire. The peer verbs
    # (`list_peers`, `agent.ask_peer`) are always-on core, not a loadable group.
    #
    # `social` is what "who are my friends" should reach. Two deliberate omissions:
    # "contact" (already claimed by `records` — a word in two groups preloads both,
    # spending the budget twice) and "@" (the system prompt uses `@path` for file
    # references, so it would preload social on nearly every file turn). Matching is
    # substring, so "friend" already covers "friends".
    "social": ("friend", "roster", "people", "presence"),
    # The two halves of "let me know when Andrew logs in, and mute any messages
    # except for him for a bit" — that one sentence must preload both groups or it
    # takes two turns. Phrases, not bare words, wherever the bare word is common:
    # "know when" rather than "know", "log in" rather than "in". "presence" is
    # deliberately left to `social` (a word in two groups spends the budget twice),
    # and the group names themselves are implicit — "notify" already matches inside
    # "notification" and "notifications", since matching is substring.
    "watch": (
        "let me know when",
        "tell me when",
        "ping me when",
        "when they come online",
        "logs in",
        "log in",
        "logs on",
        "comes online",
        "goes offline",
        "keep an eye",
    ),
    "notify": (
        "mute",
        "unmute",
        "silence",
        "do not disturb",
        "snooze",
        "quiet",
        "stop bothering",
        "alert",
    ),
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
    "localtrack": (
        "localtrack",
        "experiment",
        "experiments",
        "metric",
        "metrics",
        "loss curve",
        "loss curves",
        "hyperparameter",
        "hyperparameters",
        "wandb",
        "weights and biases",
        "track run",
        "runs",
        "tracking",
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
    "records": (
        "record",
        "contact",
        "deal",
        "crm",
        "pipeline",
        "form",
        "row",
        "spreadsheet",
        "data entry",
    ),
}


def _group_of(name: str) -> str:
    """The group a tool belongs to: its namespace before the first dot. Layout verbs
    (no dot) belong to the always-present 'layout' core."""
    return name.split(".", 1)[0] if "." in name else "layout"


def _group_permitted(group: str, allowed: set[str] | None) -> bool:
    """Whether a scoped agent may reach a group at all. `layout` is app-shell control
    rather than a capability, so it stays loadable for every agent — scoping only
    decides whether its arrangement verbs cost schema space up front."""
    return allowed is None or group in allowed or group == "layout"


def _layout_core(spec: AgentSpec | None) -> list[dict[str, Any]]:
    """The layout verbs an agent starts with: the cheap read set plus `show`, with
    the 16 arrangement verbs one `load_tools("layout")` away.

    **Including for `main`.** It used to keep all 21 unconditionally, on the reasoning
    that driving the shell is its job — but that made the *loosely prompted* agent the
    most starved one, carrying ~1.4k tokens of geometry schemas on turns that only
    wanted to read a file. `show` covers "open/go to X" in one call, and the narrow
    `layout` keyword list (`pane`, `dock`, `split`, …) preloads the arrangement verbs
    on the turns that genuinely rearrange things. A spec that names `layout`
    explicitly still opts back into all of them up front.
    """
    if (
        spec is not None
        and spec.tool_groups is not None
        and "layout" in spec.tool_groups
    ):
        return list(LAYOUT_TOOLS)
    return list(LAYOUT_READ_TOOLS)


def _core_tools(spec: AgentSpec | None = None) -> list[dict[str, Any]]:
    """Always-present tools: the layout verbs (read-only for a scoped agent, see
    `_layout_core`), the peer/delegate tools (spec-gated), the meta tools, and the
    *ungrouped* backend-plugin agent tools (registered via backend.sdk). Plugin
    tools that declare a `group` are disclosed progressively instead (see
    `_all_dynamic_tools`). `spec=None` (the main orchestrator and every pre-roster
    caller) keeps the full core."""
    from backend.sdk.registry import registry as _plugins

    tools = _layout_core(spec)
    if spec is None or spec.include_peer_tools:
        tools += list(PEER_TOOLS)
    if spec is None or spec.can_delegate:
        tools += list(DELEGATE_TOOLS)
    tools += list(META_TOOLS)
    tools += _skill_tools()
    core_plugin = _plugins.provider_tools(grouped=False)
    if spec is not None and spec.tool_groups is not None:
        allowed = set(spec.tool_groups)
        core_plugin = [
            t for t in core_plugin if _group_of(t["function"]["name"]) in allowed
        ]
    return tools + core_plugin


def _all_dynamic_tools(
    conn: WsConnection, spec: AgentSpec | None = None
) -> list[dict[str, Any]]:
    """Every progressively-disclosed tool — the layout arrangement verbs (for a spec
    that doesn't carry them in core), grouped backend-plugin tools, and every
    browser-pushed tool — deduped against **this spec's** core (core wins).

    The dedupe base has to be spec-aware: computed against the unscoped core it would
    swallow the layout write verbs for every agent, and `load_tools("layout")` would
    silently return nothing."""
    from backend.sdk.registry import registry as _plugins

    seen = {t["function"]["name"] for t in _core_tools(spec)}
    out: list[dict[str, Any]] = []
    candidates = (
        list(LAYOUT_WRITE_TOOLS)
        + list(DESKTOP_TOOLS)
        + _plugins.provider_tools(grouped=True)
        + _manifest_to_tools(getattr(conn, "agent_tools", []))
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
    it costs nothing on turns that don't touch it.

    Built-in modules are checked **first** (`guides/<group>.md`): a built-in group and
    a connector never share a name, and this ordering is what lets `layout` — which
    has no connector and no MCP server behind it — carry the geometry rules that used
    to sit in the system prompt on every single round.
    """
    from backend.modules.agent.guides import module_guide

    if text := module_guide(group):
        return text

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


def _skills_message() -> dict[str, Any] | None:
    """The skill catalog as a system message, or None when there are no skills.

    Sits next to `_guides_message` because it is the same idea one tier up: a cheap
    line that lets the model discover something expensive, delivered in full only when
    it asks. Wrapped in a swallow because a broken skills directory must never take
    down a turn — the worst it may do is cost the user their skills for that round.
    """
    try:
        from backend.modules.skills import agent as skills_agent

        return skills_agent.catalog_message()
    except Exception:  # noqa: BLE001
        logger.debug("skill catalog unavailable for this turn", exc_info=True)
        return None


def _group_catalog(
    conn: WsConnection, allowed: set[str] | None = None, spec: AgentSpec | None = None
) -> list[dict[str, Any]]:
    """The loadable groups (dynamic tools only), each with a description + count.
    `allowed` restricts the catalog to a specialized agent's tool groups (`layout`
    excepted — see `_group_permitted`)."""
    counts: dict[str, int] = {}
    for t in _all_dynamic_tools(conn, spec):
        g = _group_of(t["function"]["name"])
        if not _group_permitted(g, allowed):
            continue
        counts[g] = counts.get(g, 0) + 1
    return [
        {"name": g, "description": _group_description(g), "tools": n}
        for g, n in sorted(counts.items())
    ]


def _preload_groups(
    conn: WsConnection,
    prompt: str = "",
    history: list[Any] | None = None,
    spec: AgentSpec | None = None,
) -> set[str]:
    """Groups to activate up front from prompt/history keywords, so a typical request
    doesn't need an explicit load_tools round first."""
    text = prompt.lower()
    if history:
        for m in history:
            if isinstance(m, dict) and isinstance(m.get("content"), str):
                text += " " + m["content"].lower()
    active: set[str] = set()
    for g in {grp["name"] for grp in _group_catalog(conn, spec=spec)}:
        if g in text or any(kw in text for kw in _GROUP_KEYWORDS.get(g, ())):
            active.add(g)
    return active


#: Groups carried from one turn to the next, beyond what keywords preload. Bounded
#: because every carried group costs schema bytes on **every** later turn, and the
#: whole point of progressive disclosure is keeping the list under the reasoning
#: cliff (see docs/modules/agent-chat.mdx).
MAX_CARRIED_GROUPS = 3

#: conn → agent_id → groups that agent has loaded in this session, oldest first.
#: Keyed on the socket (weakly), so it dies with the connection and a page reload
#: starts clean — this is a within-session convenience, not persisted state.
_carried_groups: "weakref.WeakKeyDictionary[Any, dict[str, list[str]]]" = (
    weakref.WeakKeyDictionary()
)


def _carried(conn: WsConnection, agent_id: str, history: list[Any] | None) -> set[str]:
    """Groups this conversation already paid a `load_tools` round to discover.

    Without this, `active_groups` was rebuilt from keywords every turn: the model
    loaded `files` in turn 1, used it, and in turn 2 the tools were simply gone —
    so a multi-turn task re-paid discovery on every single turn, and a follow-up
    like "now delete it" arrived with nothing to delete it *with*.

    Empty `history` means a new session (the widget sends the transcript it is
    replaying), which resets the carry — that signal is exact and needs no extra
    protocol, and it is why "New chat" genuinely starts over.
    """
    if not history:
        _carried_groups.pop(conn, None)
        return set()
    return set(_carried_groups.get(conn, {}).get(agent_id, ()))


def _remember_groups(conn: WsConnection, agent_id: str, groups: set[str]) -> None:
    """Record what this turn ended up with, trimmed to `MAX_CARRIED_GROUPS`.

    Trims **oldest-first**: groups held over from earlier turns keep their order,
    anything new goes on the end, and the head falls off. The newest are the ones
    most likely to be about the thread the conversation is currently on.

    Ordering *within* a turn is arbitrary — the set carries no history — so this
    makes no claim about which of several groups loaded in one turn mattered more.
    It only guarantees the carry stays bounded.
    """
    try:
        per_agent = _carried_groups.setdefault(conn, {})
    except TypeError:  # a conn that can't be weak-referenced (a test double)
        return
    previous = per_agent.get(agent_id, [])
    ordered = [g for g in previous if g in groups]
    ordered += [g for g in sorted(groups) if g not in ordered]
    per_agent[agent_id] = ordered[-MAX_CARRIED_GROUPS:]


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
    for t in _all_dynamic_tools(conn, spec):
        group = _group_of(t["function"]["name"])
        if group in active_groups and _group_permitted(group, allowed):
            selected.append(t)
    if stats is not None:
        stats["selected"] = len(selected)
    if len(selected) > TOOL_BUDGET:
        dropped = [t["function"]["name"] for t in selected[TOOL_BUDGET:]]
        # ERROR, not WARNING, and naming the casualties: a truncated tool list is
        # indistinguishable from a model that simply chose not to use the tool, so
        # this is the only trace of *why* an agent could not do what it was asked.
        logger.error(
            "tool list %d exceeds budget %d; DROPPING %d tool(s): %s (active groups: %s)",
            len(selected),
            TOOL_BUDGET,
            len(dropped),
            ", ".join(dropped),
            sorted(active_groups),
        )
        if stats is not None:
            stats["dropped"] = dropped
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
    # The frontend clips oversized buffers to `agent.activeBufferBudget` and flags it
    # here. This flag is load-bearing, not cosmetic: the normal instruction below tells
    # the model to write the COMPLETE buffer back, and doing that from a clipped copy
    # would silently truncate the user's file. So a clipped buffer gets the opposite
    # instruction — re-read before any whole-file write.
    truncated = bool(snap.get("truncated"))
    opening = (
        f'The user is editing an open buffer "{title}" (uri: {uri}). Its content is '
        "between the markers, but it was TOO LARGE to include and is CUT OFF:"
        if truncated
        else f'The user is editing an open buffer "{title}" (uri: {uri}). Its current '
        "full content is between the markers:"
    )
    parts = [opening, "<<<BUFFER", content, "BUFFER>>>"]
    selection = snap.get("selection")
    sel_text = selection.get("text") if isinstance(selection, dict) else None
    if isinstance(sel_text, str) and sel_text:
        parts.append(f"The user's current selection within it is: {sel_text!r}")
    if truncated:
        parts.append(
            "Because the content above is incomplete, you must NOT write the whole "
            "buffer back — that would delete everything past the cut. Read the full "
            "file first (files.read, or get_pane_context on this pane), then make a "
            "targeted edit."
        )
    else:
        parts.append(
            "When the user asks to alter/refactor/fix/format this code, modify THIS "
            "content and write the complete updated buffer back with "
            f'editor.proposeEdit(uri="{uri}"). Do not call list_open_panes or '
            "get_pane_context for it first — you already have its content here."
        )
    return {"role": "system", "content": "\n".join(parts)}


def _workspace_context_message(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """A system message describing what the user's current workspace is showing.

    The companion to `_active_editor_message`: that one hands over the focused
    buffer's full text, this one is a compact index of every *other* visible pane's
    snapshot. It's what makes a preset workspace a role rather than furniture — the
    agent in a records workspace sees which record is open without spending a
    list_open_panes + get_pane_context round on it. The frontend has already applied
    the pane/char budget (`agent.workspaceContext*` settings); this only formats."""
    if not isinstance(context, dict):
        return None
    panes = context.get("panes")
    if not isinstance(panes, list) or not panes:
        return None
    lines = [
        "The user's current workspace is showing these panes. This is a live "
        "snapshot — treat it as what the user can see right now, and do NOT call "
        "list_open_panes or get_pane_context for a pane already described here "
        "(use get_pane_context only to read past a clipped field):"
    ]
    for pane in panes:
        if not isinstance(pane, dict):
            continue
        snapshot = pane.get("snapshot")
        if not isinstance(snapshot, dict):
            continue
        title = pane.get("title") or pane.get("viewId") or "pane"
        lines.append(
            f"- {title} (view: {pane.get('viewId')}, instanceId: "
            f"{pane.get('instanceId')}): {json.dumps(snapshot, default=str)}"
        )
    # Only the header survived — every entry was malformed, so say nothing.
    return {"role": "system", "content": "\n".join(lines)} if len(lines) > 1 else None


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


def _coerce_group_list(raw: Any) -> list[str]:
    """The `groups` argument of `load_tools`, however the model spelled it.

    Small models routinely hand an *array* parameter back as a string: measured
    against llama-3.2-3b through LM Studio, `groups` arrived as `'["github"]'` and
    as `"['files', 'editor']"` — the second not even valid JSON. The old code did
    `if isinstance(requested, str): requested = [requested]`, which turned
    `'["github"]'` into the single group name `["github"]`, matched nothing, and
    returned `loaded: []` with the whole thing under `unknown`.

    That failure is close to invisible and expensive: the model asked for the right
    group, was told it loaded nothing, and the capability it needed never appeared.
    It reads as "this model cannot use progressive disclosure" when what actually
    happened is that we did not read its answer. Found through the evals module,
    where every `discovery-*` case failed this way.

    `_coerce_args` does the same job one level up, for the arguments payload as a
    whole; this is the nested equivalent, and the reason the outer one did not catch
    it is that the payload itself was valid JSON — it just had a string where an
    array belonged.
    """
    import ast

    if isinstance(raw, list):
        return [str(g).strip() for g in raw if str(g).strip()]
    if not isinstance(raw, str):
        return []

    text = raw.strip()
    if not text:
        return []
    # Only try list parsing when it looks like a list. A bare `github` is a group
    # name and must not go near a parser that might read it as something else.
    if text.startswith("[") and text.endswith("]"):
        for parse in (json.loads, ast.literal_eval):
            try:
                value = parse(text)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, list):
                return [str(g).strip() for g in value if str(g).strip()]
        # Neither parser could read it — fall back to splitting on commas and
        # stripping the punctuation, which recovers `[files, editor]` and other
        # not-quite-JSON shapes rather than failing the whole call over quoting.
        return [
            part.strip().strip("\"'")
            for part in text[1:-1].split(",")
            if part.strip().strip("\"'")
        ]
    # A comma-separated string with no brackets: `files, editor`.
    if "," in text:
        return [part.strip().strip("\"'") for part in text.split(",") if part.strip()]
    return [text]


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
    # Malformed arguments are reported, never run. Executing a call whose payload
    # failed to parse would mean running it with `{}` — a `close_pane` with no
    # instanceId, a `files.delete` with no path — and the model would see a plain
    # failure with no hint that its JSON was the problem. See `_coerce_args`.
    if getattr(call, "arg_error", None):
        logger.warning(
            "tool %s called with unparseable arguments: %s", name, call.arg_error
        )
        return {
            "error": f"could not read the arguments for {name}: {call.arg_error}. "
            "Call it again with a single valid JSON object."
        }
    if name == "list_tool_groups":
        return {
            "groups": _group_catalog(conn, allowed, spec),
            "loaded": sorted(active_groups),
        }
    if name == "load_tools":
        requested = _coerce_group_list(call.arguments.get("groups"))
        available = {g["name"] for g in _group_catalog(conn, allowed, spec)}
        loaded = [g for g in requested if g in available]
        active_groups.update(loaded)
        tools_now = [
            t["function"]["name"]
            for t in _all_dynamic_tools(conn, spec)
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

    if name == "use_skill":
        from backend.modules.skills import agent as skills_agent

        # `allowed-tools` is resolved to groups and activated in this same step, so a
        # skill that says "use editor.proposeEdit" arrives with that tool already in
        # hand. Asking the model to notice the gap and call load_tools itself is a
        # round-trip it routinely skips, and then the skill's instructions name tools
        # it cannot see.
        before = set(active_groups)
        result = skills_agent.use(str(call.arguments.get("name") or ""), active_groups)
        if allowed is not None:
            # A scoped agent cannot widen its own scope through a skill.
            refused = sorted((active_groups - before) - allowed)
            if refused:
                active_groups.difference_update(refused)
                result["refusedGroups"] = refused
        # The guide for a group the skill just activated has to ride along, for the
        # same reason `load_tools` returns one: nothing else will deliver it this turn.
        if guide := _guides_text(set(result.get("loadedGroups") or [])):
            result["guide"] = guide
        return result

    # Forgiveness: the model called a known tool from a group it hadn't loaded — pull
    # the group in (so it stays visible next round) and run the call. A scoped
    # agent gets no forgiveness outside its allowed groups: the call is refused.
    if any(t["function"]["name"] == name for t in _all_dynamic_tools(conn, spec)):
        group = _group_of(name)
        if not _group_permitted(group, allowed):
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


async def _finish_capture(turn_id: str, info: Any, endpoint: str, model: str) -> None:
    """Close the interpretability capture for this turn and stamp the model's real
    context window.

    `modelContextLength` is the denominator of the pane's budget bar — the
    difference between "my prompt fit" and "my prompt was silently truncated" — and
    only the server can answer it, so it lands once at the end rather than per
    round. The probe is cached and short-timeout (see `window.py`); a provider that
    cannot answer leaves the field None, which the pane already renders as unknown
    rather than guessing. Same lazy import and same swallow-everything contract as
    `_capture_context`: an observer that can break the thing it observes is a bug.
    """
    try:
        from backend.modules.interpretability import recorder, window

        recorder.finish_turn(
            turn_id, await window.context_length(info, endpoint, model)
        )
    except Exception:
        logger.debug("interpretability finish skipped", exc_info=True)


def _begin_trajectory(**fields: Any) -> Any:
    """Open a trajectory recording for this turn, or return None.

    Lazily imported and fully swallowed, exactly like `_capture_context`: this is
    the second observer on the loop and an observer that can break the thing it
    observes is a bug. Returns None whenever capture is off, which is the default.
    """
    try:
        from backend.modules.trajectories import recorder as traj_recorder

        return traj_recorder.begin(**fields)
    except Exception:
        logger.debug("trajectory capture skipped", exc_info=True)
        return None


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
    # Record what the agent *does*, beside the `_capture_context` call that records
    # what it was *shown*. Both are keyed by `turn_id`; both are self-swallowing.
    # `None` whenever trajectory capture is off, which is the default.
    rec = _begin_trajectory(
        conn=conn,
        turn_id=turn_id,
        parent_turn_id=parent_turn_id,
        agent_id=agent_id,
        agent_name=spec.name if spec else "",
        model=model,
        provider=str(getattr(info, "kind", "")),
        messages=messages,
        tools=tools,
        params={
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "context_size": context_size,
            "progressive": progressive,
            "mode": mode_override.value if mode_override else None,
        },
    )
    answer = "(stopped after too many steps)"
    # Stamp every request this turn makes with the turn it belongs to, so the wire
    # can be lined up against what the model was shown (telemetry/turn.py). Entered
    # outside the client so the provider calls, the tool calls they trigger, and any
    # egress a backend tool performs all carry it.
    turn_token = telemetry_turn.enter(turn_id)
    try:
        async with instrumented_client(timeout=120) as client:
            for round_no in range(MAX_ROUNDS):
                telemetry_turn.mark_round(turn_id, round_no)
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

                # Weak models sometimes narrate an action without emitting the call, so
                # retry once with an explicit nudge. This deliberately runs on EVERY
                # dialect: it used to be gated on `openai`, which meant the repair never
                # fired for Ollama — the default local provider, and the one whose small
                # models need it most. `tool_choice` is simply the extra leverage OpenAI
                # offers; where it doesn't exist the re-ask alone still recovers many
                # turns, and `chat_stream` ignores a None.
                if (
                    not result.tool_calls
                    and not forced_retry_used
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
                        tool_choice="required" if info.dialect == "openai" else None,
                        context_size=context_size,
                        max_tokens=max_tokens,
                        top_p=top_p,
                    )
                    messages.append(result.assistant_message)

                if rec:
                    rec.rounds = round_no + 1
                if not result.tool_calls:
                    answer = result.content
                    return answer
                for call in result.tool_calls:
                    started = time.monotonic()
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
                    if rec:
                        # The call and its result as ONE step — pairing them later by
                        # name and ordinal breaks the moment a round calls one twice.
                        rec.action(
                            round_no,
                            call.name,
                            call.arguments,
                            tool_result,
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                    messages.append(P.tool_result_message(info, call, tool_result))
        return answer
    except BaseException as exc:
        if rec:
            rec.fail(exc)
        raise
    finally:
        if rec:
            rec.finish(answer)
        telemetry_turn.leave(turn_token)
        await _finish_capture(turn_id, info, endpoint, model)


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
    info, endpoint = roster.resolve_provider(config, agent_id)
    model = _orchestrator_model(config.model, agent_id)
    # A remote turn gets no tools (it can't reach a browser to execute them, and must
    # not act on this machine) → active_groups=None → the loop runs tool-less.
    # A local main turn seeds its groups from prompt/history keywords and grows them
    # as the model calls load_tools (progressive disclosure, recomputed per round).
    # A specialized agent skips keyword preloading — its scope is declared: it
    # starts with spec.preload_groups and can only load within spec.tool_groups.
    # Groups the conversation already discovered stay loaded, so a follow-up turn
    # doesn't re-pay a load_tools round for tools it just used (`_carried`).
    carried = set() if remote else _carried(conn, agent_id, history)
    active_groups: set[str] | None
    if remote:
        active_groups = None
    elif spec.tool_groups is None:
        active_groups = _preload_groups(conn, prompt, history) | carried
    else:
        active_groups = set(spec.preload_groups)
        # A carry never widens a scoped agent's reach: it can only re-activate a
        # group the spec already allows.
        active_groups |= {g for g in carried if g in set(spec.tool_groups)}
        # `layout` is the one group every agent can reach regardless of scope (it's
        # shell control, not a capability), so a scoped agent still gets the
        # arrangement verbs up front when the user is plainly asking to rearrange —
        # otherwise it would burn a load_tools round on "split this pane".
        if "layout" in _preload_groups(conn, prompt, history, spec):
            active_groups.add("layout")
    mode_override = None if remote else roster.resolve_mode(spec)
    editor_msg = _active_editor_message(context)
    workspace_msg = _workspace_context_message(context)
    # Guides for groups the turn starts with — the model never calls load_tools for
    # those, so this is the only chance to hand it the usage notes.
    guides_msg = _guides_message(active_groups) if active_groups else None
    # The skill catalog: one line per enabled skill, the trigger the model decides by.
    # Not sent on a remote turn — a peer's agent runs tool-less on someone else's node,
    # so offering it `use_skill` would advertise a tool it cannot call.
    skills_msg = None if remote else _skills_message()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": spec.system_prompt},
        *([skills_msg] if skills_msg else []),
        *([guides_msg] if guides_msg else []),
        *_history_messages(history),
        # The workspace index and then the focused buffer go right before the user
        # turn so they're the freshest context the model sees (and aren't diluted by
        # prior conversation). Focused buffer last: it's the most specific.
        *([workspace_msg] if workspace_msg else []),
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
        # The loop mutated `active_groups` in place as the model loaded tools —
        # hand that forward so the next turn starts where this one ended.
        if active_groups is not None:
            _remember_groups(conn, agent_id, active_groups)
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
