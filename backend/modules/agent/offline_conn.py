"""A browser stand-in for running the real agent loop with nothing attached.

`run_agent_loop` talks to a `WsConnection` for exactly four things: the two pending
tables (`pending`, `pending_approvals`), the frontend tool manifest
(`agent_tools`), and `send_json`. `_call_frontend_tool` registers a future in
`conn.pending[call_id]` and *then* awaits `conn.send_json`, so a connection object
whose `send_json` resolves that future is a complete stand-in for a browser — with
no monkeypatching of orchestrator internals and nothing to keep in sync.

This started life as `evals/runner_agent.EvalConnection`, which is the proven
version of the idea. It lives here because agentpedia's fork needs the identical
object, and agentpedia importing evals' internals would break the rule that modules
do not reach into each other. Both callers now use this one copy.

It is also the recorder: everything the loop would have told a browser passes
through `send_json`, which makes this the one place that sees the tool calls, the
approvals and the events.

**It answers tool calls from fixtures, and that is not the whole safety story.**
Backend tools and plugin tools are resolved server-side and never reach a
connection at all, so a caller that must not cause side effects passes
`run_agent_loop(..., simulate=...)` as well. See `orchestrator.run_agent_loop`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CallRecord:
    """One tool call the model made, as the loop relayed it."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


class OfflineConnection:
    """A `WsConnection` stand-in that answers tool calls from fixtures.

    Duck-typed rather than a subclass: `WsConnection.__init__` wants a live
    websocket, and everything the orchestrator touches on it is here.
    """

    def __init__(
        self,
        agent_tools: list[dict[str, Any]],
        fixtures: dict[str, Any] | None = None,
        *,
        approve: bool = True,
    ) -> None:
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.pending_approvals: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.agent_tools = agent_tools
        self._fixtures = fixtures or {}
        self._approve = approve

        #: Every tool call the model made, in order.
        self.calls: list[CallRecord] = []
        #: Tools that were gated. Not a failure — worth knowing that a run only
        #: got through because approval was automatic.
        self.gated: list[str] = []
        self.reasoning: list[str] = []
        self.events: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.events.append(data)
        if data.get("channel") != "agent":
            return
        event = data.get("event")
        payload = data.get("data") or {}

        if event == "tool_call":
            name = str(payload.get("name") or "")
            args = payload.get("args") or {}
            self.calls.append(CallRecord(name=name, arguments=args))
            self._resolve(
                self.pending,
                payload.get("callId"),
                {"ok": True, "result": self.fixture_for(name)},
            )
        elif event == "approval_request":
            self.gated.append(str(payload.get("tool") or ""))
            self._resolve(
                self.pending_approvals,
                payload.get("approvalId"),
                {"decision": "allow_once" if self._approve else "deny"},
            )

    def fixture_for(self, name: str) -> Any:
        """What a tool returns. A tool with no fixture returns a bland success:
        the alternative is an error, and an error would make the model's *next*
        move a reaction to a broken tool rather than to the task."""
        if name in self._fixtures:
            return self._fixtures[name]
        return {"ok": True}

    @staticmethod
    def _resolve(
        table: dict[str, asyncio.Future[dict[str, Any]]],
        key: Any,
        value: dict[str, Any],
    ) -> None:
        fut = table.pop(str(key), None)
        # `done()` guards the race where the loop timed out and moved on: setting a
        # result on a cancelled future raises, and an exception raised inside
        # `send_json` would surface as a broken turn rather than as a slow tool.
        if fut is not None and not fut.done():
            fut.set_result(value)


def live_agent_tools() -> list[dict[str, Any]]:
    """The richest tool manifest any connected browser has pushed.

    Richest rather than first: a second window that has not finished registering
    its panes would otherwise be able to hand a headless run a shorter catalog than
    the one the user is actually looking at, and the same suite — or the same fork
    — would behave differently depending on which socket answered.

    Empty when no browser is attached, and a caller must say so rather than quietly
    running against backend tools only: a run graded on that catalog scores zero on
    everything UI-shaped, and looks like a model failure.
    """
    from backend.modules.ws import _active_connections

    best: list[dict[str, Any]] = []
    for conn in list(_active_connections):
        tools = getattr(conn, "agent_tools", None) or []
        if len(tools) > len(best):
            best = list(tools)
    return best
