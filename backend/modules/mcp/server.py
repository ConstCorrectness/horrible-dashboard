"""The MCP server this node *exports* — the other direction from `client.py`.

Everything else in this module connects us **to** third-party servers. This exposes
horrible-dashboard **as** one, so an external agent (Claude Desktop, another node, a
CI job) can ask what our agent has been doing: which turns ran, what they cost, how work
was delegated, and what I/O the node performed.

That makes it an **interpretability surface, not a control surface**. Every tool here is
read-only. Nothing exported can open a pane, run a command, edit a file, or start a
turn — driving this node is what the agent's own tools are for, and an external caller
holding a bearer token is not the same trust level as the user sitting in front of it.

## Why this is off by default

Two independent reasons, either of which would be sufficient:

1. **`IoEvent` captures raw headers and bodies.** Its own model docstring says the
   buffer "can hold credentials and personal data" — it is a local introspection tool
   that deliberately does not sanitize. Serving that verbatim to any client that can
   reach the port would be a credential exfiltration endpoint with a friendly name.
2. **Turn snapshots contain the user's prompts**, the focused editor buffer, and tool
   results — i.e. the contents of whatever they were working on.

So: the server only mounts when `HORRIBLE_ENABLE_MCP_SERVER=1`, it requires a bearer
token, and it **redacts content by default**. Telemetry is exposed as metadata only
(method, target, status, timing, sizes) with bodies and headers dropped outright;
turn *listings* carry no context blocks at all. Prompt text is available only through
`get_agent_turn` and only when `mcp.server.exposeContent` is explicitly turned on.

The redaction happens here rather than at the callers, so a new tool cannot forget it:
`_event_summary` and `_turn_detail` are the only paths out.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from backend.modules.interpretability import store as turn_store

logger = logging.getLogger(__name__)

# Env gate. Mirrors HORRIBLE_ENABLE_SERVER_BROWSER: a capability with real blast
# radius stays behind an explicit opt-in rather than a settings toggle, so it can't
# be switched on by anything that can write settings.
ENABLE_ENV = "HORRIBLE_ENABLE_MCP_SERVER"

# Where the exported server mounts on the existing FastAPI app.
MOUNT_PATH = "/mcp-server"

# The bearer token lives in the encrypted secrets store, never in settings — the
# settings bag is handed wholesale to the browser.
TOKEN_SECRET_KEY = "mcp_server_token"

SERVER_INSTRUCTIONS = """\
Read-only introspection for a horrible-dashboard node.

Use this to answer questions about what this node's agent has been doing: which turns
ran, which agent handled them, how many rounds and tokens they took, how work was
delegated, and what network I/O the node performed.

Everything here is read-only — there is no tool to drive the node, open panes, or run
commands. Telemetry is metadata only: request/response bodies and headers are never
returned. Turn listings carry no prompt text; use get_agent_turn for one turn's detail,
which is redacted unless the operator enabled content exposure.
"""


def is_enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "") == "1"


def expose_content() -> bool:
    """Whether turn detail may include prompt/tool-result text.

    A setting rather than an env var because it is a *degree* of disclosure on an
    already-gated surface, and the user needs to flip it while debugging.
    """
    from backend.modules.settings.routes import get_value

    return bool(get_value("mcp.server.exposeContent", False))


def get_token() -> str | None:
    from backend.modules.database.secrets_store import get_secret_or_none

    return get_secret_or_none(TOKEN_SECRET_KEY) or None


def ensure_token() -> str:
    """The server's bearer token, generating one on first use.

    Generated rather than user-chosen: this guards trajectories and telemetry, and a
    memorable password on a localhost port is how those end up readable by anything
    else running on the machine.
    """
    from backend.modules.database.secrets_store import upsert_secret

    if existing := get_token():
        return existing
    token = secrets.token_urlsafe(32)
    upsert_secret(TOKEN_SECRET_KEY, token)
    return token


# --- redaction ------------------------------------------------------------------
#
# The only two paths from internal state to an external caller.

# Metadata fields of an IoEvent that are safe to export. Everything absent from this
# list — request_headers, response_headers, request_body, response_body — is dropped.
# An allow-list, not a deny-list: a new field added to IoEvent must be consciously
# opted in rather than leaking because nobody remembered to exclude it.
_EVENT_FIELDS = (
    "id",
    "ts",
    "source",
    "method",
    "target",
    "status",
    "duration_ms",
    "request_bytes",
    "response_bytes",
    "error",
    "resource_type",
    "verdict",
)


def _event_summary(event: Any) -> dict[str, Any]:
    """One I/O event reduced to metadata. Bodies and headers never survive this."""
    return {f: getattr(event, f, None) for f in _EVENT_FIELDS}


def _turn_detail(turn: Any, *, with_content: bool) -> dict[str, Any]:
    """One turn's rounds. Block content is included only when explicitly enabled."""
    rounds: list[dict[str, Any]] = []
    for r in turn.rounds:
        blocks = [
            {
                "kind": b.kind,
                "role": b.role,
                "label": b.label,
                "tokens": b.tokens,
                # The *shape* of the context is the interpretability signal; the text
                # is the private part. Sizes always, text only on request.
                **({"content": b.content} if with_content else {}),
                "chars": b.fullChars,
            }
            for b in r.blocks
        ]
        rounds.append(
            {
                "round": r.round,
                "messageTokens": r.messageTokens,
                "toolTokens": r.toolTokens,
                "totalTokens": r.totalTokens,
                "toolsSelected": r.toolsSelected,
                "toolBudget": r.toolBudget,
                "toolsTruncated": r.toolsTruncated,
                "activeGroups": r.activeGroups,
                "tools": [
                    {"name": t.name, "group": t.group, "tokens": t.tokens}
                    for t in r.tools
                ],
                "blocks": blocks,
            }
        )
    return {
        "turnId": turn.turnId,
        "parentTurnId": turn.parentTurnId,
        "agentId": turn.agentId,
        "agentName": turn.agentName,
        "kind": turn.kind,
        "model": turn.model,
        "provider": turn.provider,
        "startedAt": turn.startedAt,
        "exact": turn.exact,
        "tokenizerSource": turn.tokenizerSource,
        "requestedNumCtx": turn.requestedNumCtx,
        "modelContextLength": turn.modelContextLength,
        "temperature": turn.temperature,
        "topP": turn.topP,
        "maxTokens": turn.maxTokens,
        "permissionMode": turn.permissionMode,
        "toolGroups": turn.toolGroups,
        "peerId": turn.peerId,
        "contentRedacted": not with_content,
        "rounds": rounds,
    }


# --- the server -----------------------------------------------------------------


def build_server() -> Any:
    """Construct the FastMCP server. Import-light so a disabled node pays nothing."""
    from mcp.server.fastmcp import FastMCP

    # stateless_http: each request stands alone, which is what we want for a server
    # mounted inside another app — no session affinity, no event store to manage.
    mcp = FastMCP(
        "horrible-dashboard",
        instructions=SERVER_INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(annotations={"readOnlyHint": True})
    def list_agent_turns(
        limit: int = 25,
        agent_id: str = "",
        since_seconds: int = 0,
        roots_only: bool = True,
    ) -> dict[str, Any]:
        """List recent agent turns, most recent first.

        Returns metadata only — no prompt text. `roots_only` hides delegated
        sub-turns so you see the turns a user started. `since_seconds` limits to the
        last N seconds (0 = no limit).
        """
        since = time.time() - since_seconds if since_seconds > 0 else None
        turns = turn_store.list_turns(
            limit=limit,
            agent_id=agent_id or None,
            since=since,
            roots_only=roots_only,
        )
        return {"turns": turns, "count": len(turns)}

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_agent_turn(turn_id: str) -> dict[str, Any]:
        """One turn in detail: every round, its token costs, and which tools were
        loaded.

        Context block text is redacted unless the operator enabled
        `mcp.server.exposeContent`; `contentRedacted` in the response says which.
        """
        turn = turn_store.get_turn(turn_id)
        if turn is None:
            return {"error": f"no turn '{turn_id}'"}
        return _turn_detail(turn, with_content=expose_content())

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_turn_tree(turn_id: str) -> dict[str, Any]:
        """A turn plus the sub-turns it delegated to specialized agents.

        This is how to see multi-agent handoffs: `main` delegating to `coder` appears
        as a child, and an `agent.ask_peer` that left this node appears as a `peer`
        leaf with no rounds (that context lives on the other user's machine).
        """
        tree = turn_store.get_tree(turn_id)
        if tree is None:
            return {"error": f"no turn '{turn_id}'"}
        return tree

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_trajectory_stats() -> dict[str, Any]:
        """Aggregate counts over stored trajectories: totals, time range, per-agent
        breakdown."""
        return turn_store.stats()

    @mcp.tool(annotations={"readOnlyHint": True})
    def list_io_events(source: str = "", limit: int = 50) -> dict[str, Any]:
        """Recent I/O the node performed — metadata only.

        `source` filters to one of: inbound, outbound, ws, browser. Request and
        response bodies and headers are never returned by this tool, regardless of
        settings: the telemetry buffer captures them raw and they routinely contain
        credentials.
        """
        from backend.modules.telemetry.recorder import recorder

        events = recorder.recent()
        if source:
            events = [e for e in events if e.source == source]
        events = events[-max(1, min(limit, 500)) :]
        return {
            "events": [_event_summary(e) for e in events],
            "count": len(events),
            "note": "metadata only; bodies and headers are never exported",
        }

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_io_summary(since_seconds: int = 300) -> dict[str, Any]:
        """Aggregate I/O over the last N seconds: counts and error rate per source."""
        from backend.modules.telemetry.recorder import recorder

        cutoff = time.time() - max(1, since_seconds)
        events = [e for e in recorder.recent() if e.ts >= cutoff]
        by_source: dict[str, dict[str, Any]] = {}
        for e in events:
            bucket = by_source.setdefault(
                e.source, {"count": 0, "errors": 0, "bytesOut": 0, "bytesIn": 0}
            )
            bucket["count"] += 1
            if e.error or (e.status or 0) >= 400:
                bucket["errors"] += 1
            bucket["bytesOut"] += e.request_bytes or 0
            bucket["bytesIn"] += e.response_bytes or 0
        return {
            "windowSeconds": since_seconds,
            "total": len(events),
            "bySource": by_source,
        }

    @mcp.tool(annotations={"readOnlyHint": True})
    def describe_node() -> dict[str, Any]:
        """What this node is: its agent roster and the tool groups each can reach.

        Use this first — it tells you which agents exist and what they're scoped to,
        which is the context every other answer here is relative to.
        """
        from backend.modules.agent import roster

        agents = [
            {
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "toolGroups": a.tool_groups,
                "canDelegate": a.can_delegate,
            }
            for a in roster.list_agents()
        ]
        return {"agents": agents, "contentExposed": expose_content()}

    return mcp


# The mounted server, kept so the app lifespan can drive its session manager. None
# when the export is disabled or failed to mount.
_server: Any = None


def mount(app: Any) -> bool:
    """Mount the exported server onto the FastAPI app if enabled. Returns whether it
    mounted, so the caller can log the reason it didn't.

    Mounting is only half the job — see `session_lifespan`.
    """
    global _server
    if not is_enabled():
        return False
    try:
        from starlette.middleware import Middleware

        from backend.modules.mcp.auth import BearerAuthMiddleware

        server = build_server()
        token = ensure_token()
        # Auth wraps the MCP ASGI app itself rather than sitting on the outer app, so
        # the guard cannot be bypassed by a route registered later.
        asgi = server.streamable_http_app()
        asgi.user_middleware.insert(
            0, Middleware(BearerAuthMiddleware, token=token, allow_paths=())
        )
        asgi.middleware_stack = asgi.build_middleware_stack()
        app.mount(MOUNT_PATH, asgi)
        _server = server
        logger.info(
            "mcp: exported server mounted at %s (token in secrets store under %s)",
            MOUNT_PATH,
            TOKEN_SECRET_KEY,
        )
        return True
    except Exception:
        logger.exception("mcp: failed to mount exported server")
        return False


@asynccontextmanager
async def session_lifespan() -> AsyncIterator[None]:
    """Run the mounted server's session manager for the app's lifetime.

    **This is required, and its absence is silent until the first request.** Starlette
    does not propagate lifespan events to apps attached with `Mount`, so the session
    manager that `streamable_http_app()` would normally start in its own lifespan never
    starts. The mount succeeds, the route exists, and then every MCP request fails with
    `RuntimeError: Task group is not initialized` — a 500 that looks like a bug in the
    protocol layer rather than a missing lifecycle hook.

    So the parent app's lifespan enters this. A no-op when the export is disabled,
    which keeps the call unconditional at the call site.
    """
    if _server is None:
        yield
        return
    try:
        async with _server.session_manager.run():
            yield
    except Exception:
        # A failure here must not take the whole backend down with it — the export is
        # an optional surface, and the app has to boot without it.
        logger.exception("mcp: exported server session manager failed")
        yield
