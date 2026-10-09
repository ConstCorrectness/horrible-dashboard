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
import contextlib
import logging
import re
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from backend.modules.agent.permissions import Mode
from backend.modules.network import protocol
from backend.modules.otel import tracing as otel_tracing
from backend.modules.settings.routes import get_value

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

# Bound the agent-to-agent fan-out: how deep a chain of peers asking peers may go.
MAX_PEER_HOPS = 3
PEER_AGENT_TIMEOUT_S = 120.0

# No `ask` here, and it is no longer offered as a setting: ASK prompts a human,
# and the human it would prompt is not the one who started the turn. It used to be
# mapped to DEFAULT, so choosing "ask" silently granted a *higher* permission level
# than its name promises. An unrecognized value falls back to PLAN — read-only — so
# a stored `ask` fails closed rather than open.
_MODE_BY_NAME = {
    "plan": Mode.PLAN,
    "default": Mode.DEFAULT,
    "acceptEdits": Mode.ACCEPT_EDITS,
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
    return await request_peer_agent(
        peer_id,
        prompt,
        origin_chain=origin_chain,
        hub=hub,
        timeout=PEER_AGENT_TIMEOUT_S,
    )


async def request_peer_agent(
    peer_id: str,
    prompt: str,
    *,
    origin_chain: list[str] | None = None,
    hub: PeerHub | None = None,
    timeout: float = PEER_AGENT_TIMEOUT_S,
) -> dict[str, Any]:
    """Send one `agent_request` and wait for its `agent_result`.

    The one place a request is built, so every caller — the chat's
    `agent.ask_peer`, research's peer sub-agents — carries the same loop guard and
    the same trace context, and gets the peer's spans back the same way.

    Trace fields are optional on the wire both ways: an older peer ignores them
    and answers without a `trace`, which simply means no remote half."""
    if hub is None:
        from backend.modules.network.hub import peer_hub as hub

    me = hub.signer.node_id
    chain = list(origin_chain or [])
    if me not in chain:
        chain.append(me)
    request_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "request_id": request_id,
        "prompt": prompt,
        "origin_chain": chain,
    }
    # W3C trace context, so the peer's turn is a child of this `execute_tool` span
    # in the same trace — and an ask for that turn's spans to come back with the
    # answer, plus which trace sinks we export to, so a peer exporting to the same
    # Opik project itself can say so and we skip sending its spans twice.
    traceparent = otel_tracing.current_traceparent()
    if traceparent:
        payload["traceparent"] = traceparent
        payload["want_spans"] = True
        sinks = _trace_sinks()
        if sinks:
            payload["trace_sinks"] = sinks
    try:
        reply = await hub.request(
            peer_id,
            protocol.AGENT_REQUEST,
            payload,
            timeout=timeout,
        )
    except KeyError:
        return {"error": f"no connected peer {peer_id}"}
    except TimeoutError:
        return {"error": "peer agent timed out"}
    data = reply.data or {}
    if traceparent and data.get("trace"):
        from backend.modules.otel import peer_spans

        try:
            # Off the loop: sqlite and protobuf. The peer is named by the envelope
            # the fabric authenticated, never by anything inside the payload.
            await asyncio.to_thread(
                peer_spans.ingest,
                data["trace"],
                sent_traceparent=traceparent,
                peer=str(getattr(reply, "src", "") or peer_id),
            )
        except Exception:  # noqa: BLE001 — the answer matters more than its trace
            logger.info("could not take in %s's spans", peer_id, exc_info=True)
    if data.get("ok"):
        return {"answer": data.get("text", "")}
    return {"error": data.get("error", "peer agent failed")}


def _trace_sinks() -> list[str]:
    """Fingerprints of this node's export destinations that have one (Opik)."""
    from backend.modules.otel import destinations

    return [
        s["detail"]["fingerprint"]
        for s in destinations.statuses()
        if s.get("detail", {}).get("fingerprint")
    ]


_FINGERPRINT = re.compile(r"^[0-9a-f]{16}$")


def _shared_sinks(data: dict[str, Any]) -> list[str]:
    """The caller's trace sinks that are also ours: same Opik, same project. A
    peer-supplied list, so only well-formed fingerprints, and only a few."""
    theirs = data.get("trace_sinks")
    if not isinstance(theirs, list):
        return []
    ours = set(_trace_sinks())
    return [
        fp
        for fp in theirs[:4]
        if isinstance(fp, str) and _FINGERPRINT.match(fp) and fp in ours
    ]


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


_TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def _valid_traceparent(data: dict[str, Any]) -> str | None:
    """A peer-supplied header is data from another machine: only a well-formed
    one is honoured, and a malformed one just means a fresh trace."""
    value = data.get("traceparent")
    return value if isinstance(value, str) and _TRACEPARENT.match(value) else None


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

    traceparent = _valid_traceparent(data)
    shared = _shared_sinks(data)
    # Send the caller this turn's spans only when both sides want it: the caller
    # asked (and sent a trace to hang them on), and this node allows it.
    from backend.modules.otel import peer_spans

    return_spans = bool(
        traceparent and data.get("want_spans") and peer_spans.returns_enabled()
    )
    bucket: otel_tracing.CaptureBucket | None = None

    async def _reply(ok: bool, text: str | None, error: str | None) -> None:
        result: dict[str, Any] = {
            "request_id": request_id,
            "ok": ok,
            "text": text,
            "error": error,
        }
        if bucket is not None and bucket.spans:
            try:
                trace = await asyncio.to_thread(
                    peer_spans.collect, bucket, exported_to=shared
                )
            except Exception:  # noqa: BLE001 — never lose the answer over its trace
                logger.info("could not encode spans for %s", env.src, exc_info=True)
                trace = None
            if trace:
                result["trace"] = trace
        await hub.send_to(env.src, protocol.AGENT_RESULT, result, re=env.msg_id)

    async def _run_and_reply() -> None:
        nonlocal bucket
        try:
            try:
                # remote=True restricts the turn to no actuating tools (no browser behind it).
                # The labels say who asked, and which of the caller's trace sinks
                # are also ours (same Opik project) — the Opik shaping keeps such a
                # turn nested in the caller's trace instead of re-rooting it.
                with (
                    otel_tracing.remote_parent(traceparent),
                    otel_tracing.labels(
                        peer_caller=env.src,
                        peer_shared_sinks=",".join(shared) or None,
                    ),
                    otel_tracing.capture()
                    if return_spans
                    else contextlib.nullcontext() as captured,
                ):
                    bucket = captured
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
