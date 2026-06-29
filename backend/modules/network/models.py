"""Pydantic models for the distributed peer fabric.

These cross two boundaries: the **peer wire** (between backend nodes) and the
**REST/`/ws` surface** (between a node and its own browser). `PeerEnvelope` is the
peer-wire frame, deliberately distinct from the user-facing `WsMessage` so the two
protocols never collide. See docs/architecture/distributed.mdx.
"""

from __future__ import annotations

import json
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


class AskPeerRequest(BaseModel):
    """Ask a connected peer's agent a question (the REST entry to `agent.ask_peer`)."""

    peer_id: NodeId
    prompt: str


class AskPeerResult(BaseModel):
    ok: bool
    answer: str | None = None
    error: str | None = None


class PeerMetrics(BaseModel):
    """Live link health for one peer, sampled by the Peer Monitor and streamed to
    the browser as `peer_metrics` over the `/ws` `network` channel."""

    node_id: NodeId
    node_name: str
    transport: Transport
    status: PeerStatus
    rtt_ms: float | None = None
    bytes_in: int = 0
    bytes_out: int = 0
    msgs_in: int = 0
    msgs_out: int = 0
    last_seen: float | None = None


# ---- Agent commons (see docs/architecture/agent-commons.mdx) -----------------------


class ProfileLink(BaseModel):
    label: str
    url: str


class CommonsProfile(BaseModel):
    """A node's public storefront in the agent commons.

    A **superset of an A2A Agent Card** — `display_name`/`headline`/`agent_capabilities`
    map to the card's `name`/`description`/skills — extended with the self-certifying
    node identity, discovery `tags`, and a signature so the profile is tamper-evident
    even when re-served by a federated index. `sig` is an Ed25519 signature over
    `canonical_profile_bytes(self)` by the holder of `public_key`; the index rejects a
    profile whose signature is bad or whose `node_id` isn't the fingerprint of
    `public_key`.
    """

    node_id: NodeId
    public_key: str  # base64 Ed25519 public key (raw 32 bytes)
    display_name: str
    headline: str = ""
    bio: str | None = None
    avatar_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    seeking: str | None = None
    agent_capabilities: list[str] = Field(default_factory=list)
    links: list[ProfileLink] = Field(default_factory=list)
    visibility: Literal["public", "unlisted"] = "public"
    sig: str | None = None


class CommonsCandidate(BaseModel):
    """One ranked search hit from the commons index — a profile plus its cosine score.
    The viewer-relative trust tier is computed client-side, not here."""

    profile: CommonsProfile
    score: float


def canonical_profile_bytes(profile: CommonsProfile) -> bytes:
    """The deterministic bytes a profile's `sig` is computed and verified over: every
    field except the signature itself, serialized with sorted keys and no whitespace.
    Signer and verifier must agree byte-for-byte, so the serialization is pinned here
    rather than relying on dict/JSON ordering."""
    data = profile.model_dump(exclude={"sig"})
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_vouch_bytes(voucher_id: NodeId, subject_id: NodeId) -> bytes:
    """The bytes a vouch's signature covers: who is vouching for whom. A vouch is a
    signed attestation by `voucher_id` that `subject_id` is trustworthy — verifiable and
    portable (a federated index could re-serve it)."""
    return json.dumps(
        {"subject": subject_id, "voucher": voucher_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
