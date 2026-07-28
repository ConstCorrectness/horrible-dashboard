"""Agent-to-agent: route an agent turn to a peer's agent and back.

Caller side: `ask_peer` sends an `agent_request` and awaits the peer's
`agent_result`, returning it into the calling turn as an ordinary tool result.

Callee side: `handle_remote_agent_request` runs the orchestrator on this node with
a `RemoteAgentConn` adapter (in place of a browser socket) and replies with the
answer. The remote turn is gated by `network.remoteAgentMode` and, crucially, runs
with **no actuating tools** in v1 — it answers from the model, it cannot drive this
machine's panes/files. Loop, hop, and timeout guards bound the fan-out.

See docs/modules/agent-chat.mdx (agent-to-agent) and docs/architecture/distributed.mdx.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from backend.modules.agent.permissions import Mode
from backend.modules.network import protocol
from backend.modules.settings.routes import get_value

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

# Bound the agent-to-agent fan-out: how deep a chain of peers asking peers may go.
MAX_PEER_HOPS = 3
PEER_AGENT_TIMEOUT_S = 120.0

_MODE_BY_NAME = {
    "plan": Mode.PLAN,
    "default": Mode.DEFAULT,
    "acceptEdits": Mode.ACCEPT_EDITS,
    "ask": Mode.DEFAULT,  # ASK prompts a human; remote turns auto-deny on ASK
    "autonomous": Mode.AUTONOMOUS,
}


def _remote_mode() -> Mode:
    name = str(get_value("network.remoteAgentMode", "plan"))
    return _MODE_BY_NAME.get(name, Mode.PLAN)


# ---- caller side ------------------------------------------------------------------


# node_id -> request_id -> task
_active_remote_turns: dict[str, dict[str, asyncio.Task[None]]] = defaultdict(dict)


async def ask_peer(
    peer_id: str,
    prompt: str,
    origin_chain: list[str] | None = None,
    hub: PeerHub | None = None,
) -> dict[str, Any]:
    """Ask a peer's agent `prompt`; return `{answer}` or `{error}`. Used as the
    backend implementation of the `agent.ask_peer` tool. `hub` defaults to the
    process-global singleton (overridable in tests)."""
    if hub is None:
        from backend.modules.network.hub import peer_hub as hub

    me = hub.signer.node_id
    chain = list(origin_chain or [])
    if me not in chain:
        chain.append(me)
    request_id = uuid.uuid4().hex
    try:
        reply = await hub.request(
            peer_id,
            protocol.AGENT_REQUEST,
            {"request_id": request_id, "prompt": prompt, "origin_chain": chain},
            timeout=PEER_AGENT_TIMEOUT_S,
        )
    except KeyError:
        return {"error": f"no connected peer {peer_id}"}
    except TimeoutError:
        return {"error": "peer agent timed out"}
    data = reply.data
    if data.get("ok"):
        return {"answer": data.get("text", "")}
    return {"error": data.get("error", "peer agent failed")}


# ---- callee side ------------------------------------------------------------------


class RemoteAgentConn:
    """A `WsConnection` stand-in for a turn driven by a remote peer. There is no
    browser behind it, so it captures the final answer instead of streaming to a UI,
    and its permission mode is forced to `network.remoteAgentMode`. `is_remote`
    tells the orchestrator's gate never to block on a human approval prompt."""

    is_remote = True

    def __init__(
        self, hub: PeerHub, dst: str, request_id: str, force_mode: Mode
    ) -> None:
        self.hub = hub
        self.dst = dst
        self.request_id = request_id
        self.force_mode = force_mode
        self.pending: dict[str, Any] = {}
        self.pending_approvals: dict[str, Any] = {}
        self.agent_tools: list[dict[str, Any]] = []
        self.answer_text: str | None = None
        self.error: str | None = None
        self._done = asyncio.Event()

    async def send_json(self, data: dict[str, Any]) -> None:
        event = data.get("event")
        payload = data.get("data") or {}

        # Relay stream tokens and reasoning back to the peer
        if event in ("token", "reasoning", "delegate_token"):
            asyncio.create_task(
                self.hub.send_to(
                    self.dst,
                    protocol.AGENT_STREAM,
                    {**payload, "event": event, "request_id": self.request_id},
                )
            )
            return

        if event == "answer":
            self.answer_text = str(payload.get("text", ""))
        elif event == "error":
            self.error = str(payload.get("message", "remote agent error"))
            self._done.set()
        elif event == "done":
            self._done.set()

    async def wait_done(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
        except TimeoutError:
            self.error = self.error or "remote agent turn timed out"


async def handle_remote_agent_request(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    """Run a peer's agent request on this node and reply with `agent_result`."""
    data = env.data
    request_id = str(data.get("request_id") or env.msg_id)
    prompt = str(data.get("prompt", ""))
    origin_chain = data.get("origin_chain") or []
    me = hub.signer.node_id

    async def reject(reason: str) -> None:
        await hub.send_to(
            env.src,
            protocol.AGENT_RESULT,
            {"request_id": request_id, "ok": False, "error": reason},
            re=env.msg_id,
        )

    # Admission + loop/hop guards.
    if not get_value("network.allowRemoteAgent", False):
        await reject("remote agent access is disabled on this node")
        return
    if not session.info.trusted:
        await reject("peer not trusted")
        return
    if me in origin_chain:
        await reject("request loop detected")
        return
    if len(origin_chain) > MAX_PEER_HOPS:
        await reject("max peer hops exceeded")
        return

    from backend.modules.agent.orchestrator import run_agent_turn

    rconn = RemoteAgentConn(hub, env.src, request_id, _remote_mode())

    async def _reply(ok: bool, text: str | None, error: str | None) -> None:
        await hub.send_to(
            env.src,
            protocol.AGENT_RESULT,
            {"request_id": request_id, "ok": ok, "text": text, "error": error},
            re=env.msg_id,
        )

    async def _run_and_reply() -> None:
        try:
            try:
                # remote=True restricts the turn to no actuating tools (no browser behind it).
                await run_agent_turn(rconn, request_id, prompt, remote=True)  # type: ignore[arg-type]
                await rconn.wait_done(timeout=PEER_AGENT_TIMEOUT_S)
            except Exception as exc:  # never let a remote turn crash the session
                logger.exception("remote agent turn failed")
                rconn.error = str(exc)
            ok = rconn.error is None and rconn.answer_text is not None
            await _reply(ok, rconn.answer_text, rconn.error)
        except asyncio.CancelledError:
            # handle_remote_agent_cancel cancelled us. The peer is blocked on a
            # reply keyed to this msg_id, so it still needs one — awaiting here is
            # safe because the cancellation has already been delivered and caught.
            logger.info("remote agent turn %s cancelled by %s", request_id, env.src)
            await _reply(False, None, "cancelled")
        finally:
            _active_remote_turns[env.src].pop(request_id, None)

    # Detached on purpose: the session pump awaits handlers inline, so awaiting the
    # turn here would block the receive loop for its whole duration — the peer's
    # own agent_cancel could never be dispatched, and cancelling would unwind a
    # CancelledError into the pump and tear the link down.
    _active_remote_turns[env.src][request_id] = asyncio.create_task(_run_and_reply())


async def handle_remote_agent_cancel(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    """Stop an ongoing remote agent turn requested by this peer."""
    request_id = env.data.get("request_id")
    if not request_id:
        # If no specific request_id, cancel all turns for this peer
        turns = _active_remote_turns.pop(env.src, {})
        for task in turns.values():
            task.cancel()
        return

    task = _active_remote_turns[env.src].pop(str(request_id), None)
    if task:
        logger.info("Cancelling remote agent turn %s for %s", request_id, env.src)
        task.cancel()
