"""REST surface for the peer fabric, mounted at `/api/network`.

Request/response config lives here (identity, peer list, invite generate/redeem,
connect/disconnect); live presence streams over the `/ws` `network` channel. Same
split as clubhouse (REST) + agent (`/ws`).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.modules.network import trust
from backend.modules.network.hub import peer_hub
from backend.modules.network.models import (
    AskPeerRequest,
    AskPeerResult,
    BenchRequest,
    ConnectRequest,
    InviteResponse,
    NodeIdentity,
    PairRequest,
    PairResult,
    PeersSnapshot,
)

router = APIRouter(prefix="/network", tags=["network"])


@router.get("/identity", response_model=NodeIdentity)
def get_identity() -> NodeIdentity:
    return peer_hub.identity()


@router.get("/peers", response_model=PeersSnapshot)
def get_peers() -> PeersSnapshot:
    return peer_hub.snapshot()


@router.post("/invite", response_model=InviteResponse)
def create_invite() -> InviteResponse:
    """Mint a single-use invite the redeemer pairs with. The address advertises how
    to reach this node directly; relay/LAN refine reachability in a later slice."""
    me = peer_hub.identity()
    address = trust.advertised_address()
    invite, token, expires = trust.make_invite(address, me.node_id)
    return InviteResponse(invite=invite, token=token, expires=expires)


@router.post("/pair", response_model=PairResult)
async def pair(body: PairRequest) -> PairResult:
    try:
        address, token = trust.parse_invite(body.invite)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid invite: {exc}") from exc
    try:
        info = await peer_hub.connect(address, "direct", token=token)
        return PairResult(ok=True, peer=info)
    except Exception as exc:
        return PairResult(ok=False, error=str(exc))


@router.post("/connect", response_model=PairResult)
async def connect(body: ConnectRequest) -> PairResult:
    if not body.address:
        raise HTTPException(status_code=400, detail="address required")
    try:
        info = await peer_hub.connect(body.address, body.transport)
        return PairResult(ok=True, peer=info)
    except Exception as exc:
        return PairResult(ok=False, error=str(exc))


@router.delete("/peers/{node_id}")
async def disconnect(node_id: str) -> dict[str, bool]:
    await peer_hub.disconnect(node_id)
    return {"ok": True}


#: One bench at a time, process-wide. Two concurrent runs would each measure the
#: other's traffic and report it as this link's latency -- the one failure mode a
#: measurement tool must not have.
_bench_lock = asyncio.Lock()


@router.post("/bench")
async def run_bench(body: BenchRequest) -> dict[str, Any]:
    """Time the link to a peer, or this machine's own crypto floor.

    Returns raw dicts rather than a `response_model`: `BenchResult.to_dict` is
    already the wire shape, and a Pydantic model in front of it would silently
    drop any phase added later.
    """
    from backend.modules.network import bench

    if _bench_lock.locked():
        raise HTTPException(status_code=409, detail="a bench is already running")

    async with _bench_lock:
        if body.mode == "local":
            return {"results": [bench.run_local().to_dict()]}

        info = next(
            (p for p in peer_hub.list_peers() if p.node_id == body.node_id), None
        )
        if info is None or info.status != "connected":
            raise HTTPException(status_code=404, detail="peer is not connected")

        count = max(1, min(body.count, 200))
        if body.mode == "sweep":
            results = await bench.run_sweep(
                peer_hub, body.node_id, count=min(count, 40), transport=info.transport
            )
            return {"results": [r.to_dict() for r in results]}
        result = await bench.run_echo(
            peer_hub, body.node_id, count=count, transport=info.transport
        )
        return {"results": [result.to_dict()]}


@router.get("/leases")
def get_leases() -> dict[str, Any]:
    """Every live lease in both directions, plus this node's lending stance.

    The stance travels with the list because the two are read together: an empty
    `granted` list means something different on a node that has lending switched
    off than on one that is simply idle.
    """
    from backend.modules.network.lease import leases, lease_policy, lending_enabled

    return {
        **leases.snapshot(),
        "lending": {"enabled": lending_enabled(), "policy": lease_policy()},
    }


@router.delete("/leases/{lease_id}")
async def end_lease(lease_id: str) -> dict[str, Any]:
    """End a lease: give back one borrowed, or reclaim one lent."""
    from backend.modules.network.lease import leases

    result = await leases.end(peer_hub, lease_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=str(result.get("reason")))
    return result


@router.post("/ask-peer", response_model=AskPeerResult)
async def ask_peer_route(body: AskPeerRequest) -> AskPeerResult:
    """Ask a connected peer's agent a question and return its answer. The remote
    turn runs gated and read-only-by-default on the peer's node (it must have
    `network.allowRemoteAgent` enabled). Thin REST wrapper over the same
    `agent_bridge.ask_peer` the `agent.ask_peer` tool uses."""
    from backend.modules.network.agent_bridge import ask_peer

    result = await ask_peer(body.peer_id, body.prompt)
    if "answer" in result:
        return AskPeerResult(ok=True, answer=str(result["answer"]))
    return AskPeerResult(ok=False, error=str(result.get("error", "ask failed")))
