"""The two Ed25519 primitives the game server needs to verify a person binding.

**Why these are re-implemented rather than imported.** The node's versions live in
`backend/modules/social/identity.py` and `backend/modules/network/identity.py`,
and importing either would drag the node's module graph (settings routes, the data
directory, the peer fabric) into a service that deploys on its own to Fly and has
no business knowing about any of it.

That leaves a duplication, which is the dangerous kind: it fails **silently and
open** — a fingerprint scheme that drifts by one character makes every binding
signature fail to verify, and a verify() that drifts makes it succeed when it
shouldn't. So it is handled the same way the Kotlin wire is: the copies are tiny,
they are pure, and `backend/tests/test_games_person_binding.py` asserts they agree
with the node's implementations byte for byte. The fixture pins *agreement*; each
side's own tests pin correctness.

The scheme, for the record: a person id is `base32(sha256(raw_pubkey))[:16]`,
lowercase, unpadded — **not** base64url, the trap that silently closed sockets
during phone pairing.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def fingerprint_person(public_key_b64: str) -> str:
    """The person id derived from a base64 raw Ed25519 public key."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()[:16]


def verify(public_key_b64: str, payload: bytes, signature_b64: str) -> bool:
    """Whether `signature_b64` is a valid signature over `payload` by that key.

    Never raises: a malformed key or signature is a False, not a 500. This is
    reached from an unauthenticated-ish route with attacker-supplied strings, so
    every failure mode has to collapse to "no".
    """
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64), payload)
        return True
    except Exception:
        return False
