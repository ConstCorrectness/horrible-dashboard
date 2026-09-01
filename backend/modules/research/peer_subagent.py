"""Running one research subagent on a friend's node.

The cheapest distribution in this repo, because both halves already existed: a
subagent is already an isolated unit of work with a spec in and a report out, and
`agent_bridge.ask_peer` is already an authenticated agent-to-agent RPC. No tunnel,
no lease, no new wire type — a wave of subagents is embarrassingly parallel and
`asyncio.gather` does not care which machine each one ran on.

What it is **not** is a general "run my agent over there" button, and three
constraints keep it honest:

**A remote turn runs under the peer's permissions, not ours.** The callee gates on
its own `network.allowRemoteAgent` and `network.remoteAgentMode`, which defaults
to read-only `plan`. So a remote subagent may well have *fewer tools than a local
one* — no file writes, possibly no browser. Only research-shaped work is dispatched
remotely, and a step that needs to act stays home.

**Provenance is part of the answer.** The step records which node produced it. The
verification pass grades claims by *independent publisher*, so two peers citing the
same domain must count as one source, not two — and that arithmetic is only
possible if the report says where it came from.

**A remote failure is never fatal.** A peer that declines, times out or returns
nothing falls back to running the step locally. The wave already tolerates a dead
subagent (`return_exceptions=True` plus the "every subagent failed" guard); this
adds a cheaper recovery in front of it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: A deep subagent turn is minutes of tool calls, where `ask_peer`'s default is
#: sized for a conversational question. Passed explicitly rather than raising the
#: global: a chat user waiting on "ask Rob's agent" should not wait ten minutes
#: because research needed to.
PEER_SUBAGENT_TIMEOUT_S = 600.0

#: How many of one wave may go out at once. A cap rather than "as many as there
#: are peers": a research run should not be able to occupy every friend's agent,
#: and the local machine is usually the fastest path anyway.
MAX_REMOTE_PER_WAVE = 2


def eligible_peers() -> list[str]:
    """Trusted, connected peers whose agent could take a subagent step."""
    from backend.modules.network.hub import peer_hub

    out: list[str] = []
    for info in peer_hub.list_peers():
        # Trust is checked here as well as by the callee. Asking is cheap, but
        # dispatching a research objective to a stranger's machine is not the kind
        # of thing to do on the assumption that they will refuse.
        if not info.trusted or info.status != "connected":
            continue
        if "agent" in info.capabilities:
            out.append(info.node_id)
    return out


def assign(specs: list[dict[str, Any]], peers: list[str]) -> dict[int, str]:
    """Which subagent indices go to which peer.

    Round-robin over at most `MAX_REMOTE_PER_WAVE` steps. Deliberately dumb: the
    fabric has no load signal worth acting on, and a wrong guess costs a fallback
    rather than a failure.
    """
    if not peers:
        return {}
    out: dict[int, str] = {}
    for index, _spec in enumerate(specs):
        if len(out) >= MAX_REMOTE_PER_WAVE:
            break
        out[index] = peers[index % len(peers)]
    return out


def build_prompt(spec: dict[str, Any]) -> str:
    """The subagent brief, as a self-contained question for a remote agent.

    The remote side has none of our run context — no library handle, no plan, no
    sibling steps — so everything it needs has to be in the text. It is asked for
    the same `SOURCES:` shape a local subagent produces, because `_split_findings`
    parses the answer either way.
    """
    from backend.modules.research import prompts

    return prompts.SUBAGENT_PROMPT.format(
        objective=spec.get("objective", ""),
        output_format=spec.get("output_format", ""),
        tool_guidance=(
            f"{spec.get('tool_guidance', '')}\n\n"
            "You are answering as a remote research subagent for another node. "
            "Use only your own read-only research tools; do not modify anything."
        ),
        boundaries=spec.get("boundaries", ""),
        max_tool_calls=int(spec.get("max_tool_calls", 10)),
    )


async def run_remote(
    spec: dict[str, Any], node_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]], int] | None:
    """Run one subagent on `node_id`, or return None to fall back locally.

    Returns the same `(output, transcript, tokens)` triple as
    `engine.run_subagent_step`, so the runner's step machinery is unchanged.
    `tokens` is 0: the tokens were spent on the peer's budget, and charging them
    to this run's budget would throttle a run for work it did not pay for.
    """
    from backend.modules.research.engine import _split_findings

    prompt = build_prompt(spec)
    try:
        reply = await _ask(node_id, prompt)
    except Exception as exc:  # noqa: BLE001 - a peer failing is a fallback, not an error
        logger.info("research: peer %s failed (%s); running locally", node_id, exc)
        return None

    if "answer" not in reply:
        logger.info(
            "research: peer %s declined (%s); running locally",
            node_id,
            reply.get("error"),
        )
        return None

    answer = str(reply.get("answer") or "").strip()
    if not answer:
        # An empty answer is a decline, not a finding. Recording it as a completed
        # step would let synthesis proceed believing this angle was covered.
        logger.info("research: peer %s returned nothing; running locally", node_id)
        return None

    findings, sources = _split_findings(answer)
    return (
        {
            "name": spec.get("name", "subagent"),
            "findings": findings,
            "sources": sources,
            "tool_calls_used": 0,
            # Read by the verification pass: two peers citing one domain are one
            # independent publisher, not two.
            "ran_on": node_id,
        },
        [{"role": "assistant", "content": answer, "ran_on": node_id}],
        0,
    )


async def _ask(node_id: str, prompt: str) -> dict[str, Any]:
    """`ask_peer` with a research-sized timeout.

    `agent_bridge.ask_peer` hardcodes its own, so the request is issued here
    rather than by raising that module's constant — a chat user waiting on "ask
    Rob's agent" should not inherit a ten-minute ceiling because research needed
    one.
    """
    import uuid

    from backend.modules.network import protocol
    from backend.modules.network.hub import peer_hub

    me = peer_hub.signer.node_id
    try:
        reply = await peer_hub.request(
            node_id,
            protocol.AGENT_REQUEST,
            {
                "request_id": uuid.uuid4().hex,
                "prompt": prompt,
                # The same loop guard `ask_peer` builds: a peer that would have to
                # come back to us to answer must not.
                "origin_chain": [me],
            },
            timeout=PEER_SUBAGENT_TIMEOUT_S,
        )
    except KeyError:
        return {"error": f"no connected peer {node_id}"}
    except TimeoutError:
        return {"error": "peer agent timed out"}

    data = reply.data or {}
    if data.get("ok"):
        return {"answer": data.get("text", "")}
    return {"error": data.get("error", "peer agent failed")}
