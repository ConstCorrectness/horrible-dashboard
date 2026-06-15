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

from backend.modules.agent import providers as P
from backend.modules.agent.routes import _load_config
from backend.modules.telemetry.instrument import instrumented_client
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

# Guard against a model that never stops calling tools.
MAX_ROUNDS = 8
TOOL_TIMEOUT_S = 30.0

SYSTEM_PROMPT = (
    "You are the orchestrator for horrible-dashboard, a dockable dashboard app. "
    "You arrange the user's screen by opening/closing panes and managing "
    "workspaces, using the provided tools.\n"
    "Rules:\n"
    "- A 'pane' is a panel or widget shown on screen (e.g. Marketplace, Settings, "
    "Data flow, Backend status). To open/show/add something the user names, FIRST "
    "call list_available_panes to find the pane whose title matches, THEN call "
    "open_pane with that exact id. Do NOT treat it as a workspace.\n"
    "- Only use list_workspaces / create_workspace / switch_workspace when the "
    "user explicitly talks about workspaces or tabs.\n"
    "- Ids are not guessable; always discover them with a list_* tool first.\n"
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
]


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "agent", "event": event, "data": data}


async def handle_agent_message(conn: WsConnection, msg: dict[str, Any]) -> None:
    """Route an inbound `agent`-channel message from the browser."""
    event = msg.get("event")
    data = msg.get("data") or {}
    if event == "ask":
        # Must not block the receive loop — the turn awaits tool_results that
        # arrive on that same loop. Run it detached.
        asyncio.create_task(
            run_agent_turn(
                conn, str(data.get("turnId", "")), str(data.get("prompt", ""))
            )
        )
    elif event == "tool_result":
        call_id = str(data.get("callId", ""))
        fut = conn.pending.pop(call_id, None)
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


async def run_agent_turn(conn: WsConnection, turn_id: str, prompt: str) -> None:
    """Drive one user turn: loop the configured provider's chat, relaying tool
    calls to the UI. The provider dialect (Ollama vs OpenAI-compatible) is hidden
    behind providers.chat / providers.tool_result_message."""
    config = _load_config()
    if config is None:
        await conn.send_json(
            _evt("error", {"turnId": turn_id, "message": "Agent not configured"})
        )
        return
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    try:
        async with instrumented_client(timeout=120) as client:
            for _ in range(MAX_ROUNDS):
                result = await P.chat(
                    client, info, endpoint, config.model, messages, LAYOUT_TOOLS
                )
                messages.append(result.assistant_message)
                if not result.tool_calls:
                    await conn.send_json(
                        _evt("answer", {"turnId": turn_id, "text": result.content})
                    )
                    await conn.send_json(_evt("done", {"turnId": turn_id}))
                    return
                for call in result.tool_calls:
                    tool_result = await _call_frontend_tool(
                        conn, turn_id, call.name, call.arguments
                    )
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
