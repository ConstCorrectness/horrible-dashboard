"""Agent tools over the peer fabric.

Everything the previous phases built — capability advertisement, the bench, the
lease protocol, the byte tunnel, borrowable extras — is machinery the agent could
not reach. This is the surface.

**All six are loadable (`group="network"`), none always-on.** Ungrouped backend
tools cost schema bytes on *every* turn, which is why the always-on core was cut
from 34 tools to 11 against a reasoning cliff that appears around 40. The two peer
verbs that stay always-on live elsewhere and are unchanged: `list_peers` (the
prerequisite `agent.ask_peer` documents) and `agent.ask_peer` itself, which is the
one peer verb a user names mid-conversation.

`find_peers` is deliberately **one tool, not four**. "Find a friend", "find an
open game", "find a peer with a GPU" and "who can transcribe this?" were three
separate mechanisms before capability v2; they are now one query over
`PeerCapability.attrs`, so the tool count does not grow when the next capability
is added.

The three lease tools take a **generic `service` string** for the same reason:
`llama`, `embed`, `voice`, `clip`, `trace` and `browser` all ride one protocol, so
these six tools cover phases 4, 6, 7 and 8 without growing either.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

#: A lease duration the agent may ask for, in minutes. The lender clamps to its
#: own `MAX_DURATION_S` regardless, so this only stops an obviously-wrong request
#: from being sent at all.
MAX_MINUTES = 60


def _peer_row(info: Any) -> dict[str, Any]:
    return {
        "node_id": info.node_id,
        "name": info.node_name,
        "status": info.status,
        "transport": info.transport,
        "trusted": info.trusted,
        "capabilities": list(info.capabilities),
        "caps": {cap.id: cap.attrs for cap in info.caps},
    }


async def survey(_args: dict[str, Any]) -> dict[str, Any]:
    """What this node's fabric looks like right now.

    One call rather than three, because the answer to "can I run this on another
    machine?" needs all of it: who is reachable, what they offer, what latency
    they are at, and which leases already exist in either direction.
    """
    from backend.modules.network.hub import peer_hub
    from backend.modules.network.lease import leases, lease_policy, lending_enabled
    from backend.modules.network.monitor import peer_monitor

    rtt = {m.node_id: m.rtt_ms for m in peer_monitor.snapshot()}
    peers = []
    for info in peer_hub.list_peers():
        row = _peer_row(info)
        row["rtt_ms"] = rtt.get(info.node_id)
        peers.append(row)

    return {
        "you": {
            "node_id": peer_hub.identity().node_id,
            "capabilities": peer_hub.capabilities(),
        },
        "peers": peers,
        "leases": leases.snapshot(),
        "lending": {
            "enabled": lending_enabled(),
            "policy": lease_policy(),
            "note": _lending_note(),
        },
    }


def _lending_note() -> str:
    """Why this node will or will not lend, in a sentence.

    Said plainly rather than left to be inferred from two flags. The commonest
    confusion here is a user who turned lending on and still sees every request
    refused, because the policy is `ask` and there is no approval UI yet.
    """
    from backend.modules.network.lease import lease_policy, lending_enabled

    if not lending_enabled():
        return "This node will not lend: network.allowComputeLending is off."
    policy = lease_policy()
    if policy == "trusted":
        return "This node lends to trusted peers."
    if policy == "ask":
        return (
            "This node will not lend yet: network.computeLeasePolicy is 'ask' and "
            "there is no approval UI, so requests are refused. Set it to "
            "'trusted' to lend to trusted peers."
        )
    return "This node will not lend: network.computeLeasePolicy is 'off'."


async def measure_peer(args: dict[str, Any]) -> dict[str, Any]:
    """Time the link to a peer.

    Reports **percentiles, never a mean**: the distribution here is bimodal when
    something is blocking the pump, and a mean is exactly the statistic that hides
    that.
    """
    from backend.modules.network import bench
    from backend.modules.network.hub import peer_hub

    mode = str(args.get("mode") or "echo").lower()
    if mode == "local":
        # No link at all — the CPU floor for sign/serialize/verify. Worth having
        # because it separates "this peer is far away" from "this machine is slow".
        return {"results": [bench.run_local().to_dict()]}

    node_id = str(args.get("node") or "").strip()
    if not node_id:
        return {"error": "which peer? pass a node id, or mode='local'"}
    info = next((p for p in peer_hub.list_peers() if p.node_id == node_id), None)
    if info is None or info.status != "connected":
        return {"error": f"{node_id} is not connected"}

    try:
        count = max(1, min(int(args.get("count") or 40), 200))
    except (TypeError, ValueError):
        count = 40

    if mode == "sweep":
        results = await bench.run_sweep(
            peer_hub, node_id, count=min(count, 40), transport=info.transport
        )
        return {"results": [r.to_dict() for r in results]}

    result = await bench.run_echo(
        peer_hub, node_id, count=count, transport=info.transport
    )
    return {"results": [result.to_dict()]}


def _matches(attrs: dict[str, Any], attr: str, contains: str, at_least: Any) -> bool:
    """Whether one capability's attrs satisfy the query.

    A **missing attribute never matches**. The alternative — treating absence as
    zero, or as a pass — would answer "who has 8 GB of VRAM?" with a node that
    never said, and the whole point of the three-state discipline elsewhere in
    this repo is that "did not say" is its own answer.
    """
    if not attr:
        return True
    if attr not in attrs:
        return False
    value = attrs[attr]
    if contains:
        if isinstance(value, list):
            if contains not in [str(v) for v in value]:
                return False
        elif str(value) != contains:
            return False
    if at_least is not None:
        try:
            if float(value) < float(at_least):
                return False
        except (TypeError, ValueError):
            return False
    return True


async def find_peers(args: dict[str, Any]) -> dict[str, Any]:
    """Peers offering a capability, optionally filtered on its live attributes."""
    from backend.modules.network.hub import peer_hub

    cap_id = str(args.get("capability") or "").strip()
    if not cap_id:
        return {"error": "which capability? e.g. 'inference', 'extras', 'hassault'"}
    attr = str(args.get("attr") or "").strip()
    contains = str(args.get("contains") or "").strip()
    at_least = args.get("atLeast")

    matches = []
    for info in peer_hub.list_peers():
        if info.status != "connected":
            continue
        for cap in info.caps:
            if cap.id != cap_id:
                continue
            if not _matches(cap.attrs, attr, contains, at_least):
                continue
            row = _peer_row(info)
            row["matched"] = {"capability": cap_id, "attrs": cap.attrs}
            matches.append(row)
            break

    return {
        "peers": matches,
        # Named so a bare empty list is not read as "nobody has a GPU": an
        # untrusted or disconnected peer is invisible here for reasons that have
        # nothing to do with what it can do.
        "searched": len(peer_hub.list_peers()),
    }


def _minutes(args: dict[str, Any], default: float) -> float:
    try:
        minutes = float(args.get("durationMinutes") or 0) or (default / 60.0)
    except (TypeError, ValueError):
        minutes = default / 60.0
    return max(1.0, min(minutes, MAX_MINUTES)) * 60.0


async def request_compute(args: dict[str, Any]) -> dict[str, Any]:
    """Ask a peer for a compute lease and open its tunnel.

    A denial is an **answer**, not an error: the far side decides, and its reason
    ("this node is serving X, ask for that") is usually actionable.
    """
    from backend.modules.network.hub import peer_hub
    from backend.modules.network.lease import DEFAULT_DURATION_S, leases

    node_id = str(args.get("node") or "").strip()
    service = str(args.get("service") or "").strip()
    if not node_id or not service:
        return {"error": "need a node id and a service (llama, embed, voice, ...)"}
    model = str(args.get("model") or "").strip() or None

    try:
        borrowed = await leases.request(
            peer_hub,
            node_id,
            service,
            model=model,
            duration_s=_minutes(args, DEFAULT_DURATION_S),
        )
    except PermissionError as exc:
        return {"granted": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a dead peer is a reportable answer
        return {"granted": False, "reason": f"{node_id}: {exc}"}

    return {
        "granted": True,
        "leaseId": borrowed.lease_id,
        "node": node_id,
        "service": service,
        "model": borrowed.model,
        "expiresAt": borrowed.expires_at,
        # The tunnel endpoint is deliberately reported. For `service="llama"` the
        # agent does not need to use it — setting an agent's provider to `peer`
        # resolves it automatically — but for `embed`/`voice` it is the address
        # the work goes to.
        "endpoint": borrowed.endpoint,
    }


async def renew_lease(args: dict[str, Any]) -> dict[str, Any]:
    """Extend a lease this node holds, before it expires."""
    from backend.modules.network.hub import peer_hub
    from backend.modules.network.lease import DEFAULT_DURATION_S, leases

    lease_id = str(args.get("leaseId") or "").strip()
    try:
        borrowed = await leases.renew_borrowed(
            peer_hub, lease_id, duration_s=_minutes(args, DEFAULT_DURATION_S)
        )
    except KeyError:
        return {"ok": False, "reason": f"no lease {lease_id!r} is held here"}
    except PermissionError as exc:
        return {"ok": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "leaseId": lease_id, "expiresAt": borrowed.expires_at}


async def release_lease(args: dict[str, Any]) -> dict[str, Any]:
    """End a lease in either direction.

    One tool for both because the user's intent is one thing — "stop this" — and
    which side of it we are on is a fact the code can look up. Giving back a
    borrowed lease and reclaiming a lent one are the same sentence.
    """
    from backend.modules.network.hub import peer_hub
    from backend.modules.network.lease import leases

    return await leases.end(peer_hub, str(args.get("leaseId") or "").strip())


_NODE_PARAM = {
    "type": "string",
    "description": "The peer's node id, as reported by network.survey or list_peers.",
}
_DURATION_PARAM = {
    "type": "number",
    "description": (
        f"How long to ask for, in minutes (default 15, capped at {MAX_MINUTES}). "
        "The lender clamps this to its own limit and may refuse."
    ),
}


def register_network_tools() -> None:
    registry.agent_tools["network.survey"] = AgentTool(
        name="network.survey",
        description=(
            "Survey the peer fabric: this node's id and capabilities, every known "
            "peer with status/transport/trust/latency and what each offers, all "
            "active compute leases in both directions, and whether this node is "
            "willing to lend. Start here before borrowing anything."
        ),
        handler=survey,
        group="network",
    )
    registry.agent_tools["network.measure_peer"] = AgentTool(
        name="network.measure_peer",
        description=(
            "Measure link latency to a peer, reported as p50/p90/p99 per phase "
            "(sign, serialize, verify, dispatch, handler). Use before deciding "
            "whether to distribute work: latency-tolerant batches are fine over a "
            "slow link, streaming is not."
        ),
        handler=measure_peer,
        group="network",
        parameters={
            "node": _NODE_PARAM,
            "mode": {
                "type": "string",
                "enum": ["echo", "sweep", "local"],
                "description": (
                    "'echo' (default) times small round trips; 'sweep' repeats at "
                    "64B–1MB to show how throughput scales with payload; 'local' "
                    "needs no peer and measures this machine's own crypto floor."
                ),
            },
            "count": {
                "type": "number",
                "description": "Round trips to time (default 40, max 200).",
            },
        },
    )
    registry.agent_tools["network.find_peers"] = AgentTool(
        name="network.find_peers",
        description=(
            "Find connected peers offering a capability, optionally filtered on "
            "its live attributes. This is the one way to answer 'who has a GPU "
            "free', 'who has an open game', 'who can transcribe this'. "
            "Capabilities include: 'inference' (attrs: accelerator, vramMb, "
            "models, serving), 'extras' (attrs: installed — voice, clip, trace, "
            "browser), 'hassault' (attrs: openMatches, maxPlayers), 'agent', "
            "'collab', 'share'."
        ),
        handler=find_peers,
        group="network",
        parameters={
            "capability": {
                "type": "string",
                "description": "The capability id, e.g. 'inference' or 'extras'.",
            },
            "attr": {
                "type": "string",
                "description": (
                    "An attribute of that capability to filter on. A peer that "
                    "does not report the attribute never matches."
                ),
            },
            "contains": {
                "type": "string",
                "description": (
                    "Require the attribute to equal this, or (for a list "
                    "attribute like 'installed' or 'models') to contain it."
                ),
            },
            "atLeast": {
                "type": "number",
                "description": "Require a numeric attribute to be at least this.",
            },
        },
        required=["capability"],
    )
    registry.agent_tools["network.request_compute"] = AgentTool(
        name="network.request_compute",
        description=(
            "Ask a peer to lend compute, and open the tunnel that makes it "
            "usable. Services: 'llama' (run chat on their GPU — then set an "
            "agent's provider to 'peer'), 'embed' (embedding batches), 'voice', "
            "'clip', 'trace', 'browser'. The peer decides; a refusal comes back "
            "with its reason. Lending is off by default on every node."
        ),
        handler=request_compute,
        group="network",
        parameters={
            "node": _NODE_PARAM,
            "service": {
                "type": "string",
                "description": (
                    "What to borrow: llama, embed, voice, clip, trace, browser."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "For 'llama': which model to ask for. Peers lend only the "
                    "model already loaded unless configured otherwise, so prefer "
                    "the one network.find_peers reports as 'serving'."
                ),
            },
            "durationMinutes": _DURATION_PARAM,
        },
        required=["node", "service"],
        side_effect=True,
    )
    registry.agent_tools["network.renew_lease"] = AgentTool(
        name="network.renew_lease",
        description=(
            "Extend a compute lease this node holds before it expires. The "
            "lender may refuse or shorten it; the returned expiry is theirs."
        ),
        handler=renew_lease,
        group="network",
        parameters={
            "leaseId": {"type": "string", "description": "The lease id."},
            "durationMinutes": _DURATION_PARAM,
        },
        required=["leaseId"],
        side_effect=True,
    )
    registry.agent_tools["network.release_lease"] = AgentTool(
        name="network.release_lease",
        description=(
            "End a compute lease: give back one borrowed from a peer, or reclaim "
            "one lent to them. Closes its tunnel immediately."
        ),
        handler=release_lease,
        group="network",
        parameters={"leaseId": {"type": "string", "description": "The lease id."}},
        required=["leaseId"],
        side_effect=True,
    )
