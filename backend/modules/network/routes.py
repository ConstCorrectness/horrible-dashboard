"""REST surface for the peer fabric, mounted at `/api/network`.

Request/response config lives here (identity, peer list, invite generate/redeem,
connect/disconnect); live presence streams over the `/ws` `network` channel. Same
split as clubhouse (REST) + agent (`/ws`).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.modules.network import trust
from backend.modules.network.hub import peer_hub
from backend.modules.network.models import (
    AskPeerRequest,
    AskPeerResult,
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
