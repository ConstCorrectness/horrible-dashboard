"""Peer-wire serialization, signing, and the loop/replay guard.

Canonicalization must be **byte-stable across nodes** (sorted keys, fixed
separators) so a signature produced on one machine verifies on another. The
`SeenGuard` deduplicates `msg_id`s and the envelope `ttl` bounds relay hops — the
two together stop loops and replays on the fabric.
"""

from __future__ import annotations

import json
from collections import OrderedDict

from backend.modules.network import identity
from backend.modules.network.models import PeerEnvelope

# Message-type constants (the `type` field of a PeerEnvelope).
HELLO = "hello"
HELLO_ACK = "hello_ack"
AUTH = "auth"
AUTH_RESULT = "auth_result"
PRESENCE = "presence"
PING = "ping"
PONG = "pong"
AGENT_REQUEST = "agent_request"
AGENT_STREAM = "agent_stream"
AGENT_RESULT = "agent_result"
AGENT_CANCEL = "agent_cancel"
COLLAB_JOIN = "collab_join"
COLLAB_LEAVE = "collab_leave"
COLLAB_OP = "collab_op"
ERROR = "error"


def canonical_bytes(env: PeerEnvelope) -> bytes:
    """The bytes an envelope signature covers: the authenticated fields only,
    encoded with sorted keys and compact separators for cross-node byte stability.

    `dst` and `ttl` are routing headers a relay may legitimately set/rewrite while
    forwarding, so they are excluded — the recipient still authenticates `src`,
    `type`, `msg_id`, `re`, `ts`, and `data`."""
    payload = env.model_dump(exclude={"sig", "dst", "ttl"})
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_envelope(
    env: PeerEnvelope, signer: identity.Identity | None = None
) -> PeerEnvelope:
    """Return a copy of `env` signed by `signer` (this node by default; pass an
    explicit identity so a hub signs as itself regardless of process-global state).
    Also stamps `src` if unset."""
    me = signer or identity.load_identity()
    if not env.src:
        env = env.model_copy(update={"src": me.node_id})
    env.sig = me.sign(canonical_bytes(env))
    return env


def verify_envelope(env: PeerEnvelope, public_key_b64: str) -> bool:
    """Whether `env.sig` is a valid signature over the envelope by `public_key_b64`.
    The caller is responsible for checking the key's fingerprint matches `env.src`."""
    if not env.sig:
        return False
    return identity.verify(public_key_b64, canonical_bytes(env), env.sig)


def encode(env: PeerEnvelope) -> str:
    return env.model_dump_json()


def decode(raw: str) -> PeerEnvelope:
    return PeerEnvelope.model_validate_json(raw)


class SeenGuard:
    """Bounded LRU of recently-seen `msg_id`s. `check(msg_id)` returns True the first
    time an id is seen and False on a repeat, so a replayed/looped envelope is
    dropped."""

    def __init__(self, capacity: int = 4096) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._capacity = capacity

    def check(self, msg_id: str) -> bool:
        if msg_id in self._seen:
            self._seen.move_to_end(msg_id)
            return False
        self._seen[msg_id] = None
        if len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return True
