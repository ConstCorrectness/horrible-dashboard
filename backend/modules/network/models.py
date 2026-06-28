"""Pydantic models for the distributed peer fabric.

These cross two boundaries: the **peer wire** (between backend nodes) and the
**REST/`/ws` surface** (between a node and its own browser). `PeerEnvelope` is the
peer-wire frame, deliberately distinct from the user-facing `WsMessage` so the two
protocols never collide. See docs/architecture/distributed.mdx.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# Wire protocol version. Bumped only on a breaking envelope change; the handshake
# rejects a peer whose major version differs.
PROTOCOL_VERSION = 1

# A node id is the base32(sha256(pubkey))[:16] fingerprint — stable, unguessable,
# and self-certifying (it must equal the hash of the public key a peer presents).
NodeId = str

Transport = Literal["direct", "relay", "lan", "webrtc"]
PeerStatus = Literal["connected", "connecting", "disconnected", "blocked"]


class NodeIdentity(BaseModel):
    """A node's public identity. The private key never appears here — it lives only
    in the on-disk identity file (see identity.py)."""

    node_id: NodeId
    public_key: str  # base64 Ed25519 public key (raw 32 bytes)
    node_name: str
    capabilities: list[str] = Field(default_factory=list)


class PeerInfo(BaseModel):
    """What a node knows about one peer, surfaced to the browser via the `network`
    channel + `/api/network/peers`."""

    node_id: NodeId
    node_name: str
    public_key: str
    transport: Transport
    address: str | None = None
    status: PeerStatus
    trusted: bool = False
    last_seen: float | None = None
    capabilities: list[str] = Field(default_factory=list)


class PeerEnvelope(BaseModel):
    """One frame on the peer wire.

    `sig` is an Ed25519 signature over the canonical bytes of every field except
    `sig` itself (see protocol.canonical_bytes); `verify` rejects a bad signature.
    `ttl` decrements on every relay forward and `msg_id` is deduped against an LRU
    `seen` set — together the transport-level loop/replay guard.
    """

    v: int = PROTOCOL_VERSION
    type: str
    msg_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    re: str | None = None  # msg_id this is a reply to (request/reply correlation)
    src: NodeId
    dst: NodeId | None = None  # None = link-level (handshake) or broadcast
    ts: float = Field(default_factory=lambda: time.time())
    ttl: int = 8
    data: dict[str, Any] = Field(default_factory=dict)
    sig: str | None = None


# ---- REST request/response shapes -------------------------------------------------


class ConnectRequest(BaseModel):
    address: str | None = None
    node_id: NodeId | None = None
    transport: Transport = "direct"


class InviteResponse(BaseModel):
    """An invite link a peer redeems to pair. Encodes everything the redeemer needs
    to reach and trust this node."""

    invite: str  # opaque, base64url-encoded JSON bundle
    token: str
    expires: float


class PairRequest(BaseModel):
    invite: str


class PairResult(BaseModel):
    ok: bool
    peer: PeerInfo | None = None
    error: str | None = None


class PeersSnapshot(BaseModel):
    self: NodeIdentity
    peers: list[PeerInfo] = Field(default_factory=list)
