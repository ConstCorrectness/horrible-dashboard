"""Pinned wire contract for non-Python peers (the Android companion app).

The peer fabric authenticates every envelope with an Ed25519 signature over a
*canonicalization* of its fields, and derives node ids by fingerprinting a public
key. Both are byte-exact contracts: a second implementation that disagrees on a
single character — key order, a float's spelling, an escape — is rejected during
the handshake with no diagnostic beyond a closed socket.

`apps/mobile-android` reimplements both in Kotlin (`network/CanonicalJson.kt`,
`network/Identity.kt`, `network/Protocol.kt`). These vectors pin the Python side
so a refactor here can't silently break every phone in the field; the same
constants are quoted in docs/modules/mobile-android.mdx.
"""

from __future__ import annotations

import base64
import json

from backend.modules.network.identity import fingerprint
from backend.modules.network.models import PeerEnvelope
from backend.modules.network.protocol import canonical_bytes

# A fixed key so the fingerprint is reproducible: raw bytes 0x00..0x1f.
_PUBLIC_KEY = base64.b64encode(bytes(range(32))).decode("ascii")
_NODE_ID = "mmg42klgyqzwneis"


def _envelope() -> PeerEnvelope:
    return PeerEnvelope(
        type="hello",
        msg_id="0123456789abcdef0123456789abcdef",
        src=_NODE_ID,
        dst=None,
        ts=1753632000.123456,
        data={
            "node_name": "Pixel 9 café",
            "public_key": _PUBLIC_KEY,
            "capabilities": ["mobile"],
            "nonce": "n1",
        },
    )


def test_fingerprint_is_lowercase_unpadded_base32():
    """Node id = base32(sha256(pubkey)), unpadded, lowercased, first 16 chars.

    Base32 — not base64url. The alphabets overlap enough that a base64url
    implementation produces a plausible-looking id that never matches.
    """
    assert fingerprint(_PUBLIC_KEY) == _NODE_ID
    assert set(_NODE_ID) <= set("abcdefghijklmnopqrstuvwxyz234567")


def test_canonical_bytes_are_pinned():
    """The exact bytes a signature covers, for a known envelope."""
    expected = (
        '{"data":{"capabilities":["mobile"],"node_name":"Pixel 9 caf\\u00e9",'
        '"nonce":"n1","public_key":"' + _PUBLIC_KEY + '"},'
        '"msg_id":"0123456789abcdef0123456789abcdef",'
        '"re":null,"src":"' + _NODE_ID + '",'
        '"ts":1753632000.123456,"type":"hello","v":1}'
    )
    assert canonical_bytes(_envelope()).decode("utf-8") == expected


def test_canonical_bytes_properties_a_second_implementation_must_match():
    """The four rules a reimplementation gets wrong in practice."""
    raw = canonical_bytes(_envelope()).decode("utf-8")

    # 1. `re` is always present, even when null (Moshi and friends omit nulls).
    assert '"re":null' in raw

    # 2. Keys are sorted *recursively*, not just at the top level.
    payload = json.loads(raw)
    assert list(payload) == sorted(payload)
    assert list(payload["data"]) == sorted(payload["data"])

    # 3. Floats use Python's repr — never scientific notation at this magnitude,
    #    which is exactly where Java's Double.toString switches to it.
    assert '"ts":1753632000.123456' in raw
    assert "E9" not in raw and "e+" not in raw

    # 4. Non-ASCII is escaped (ensure_ascii), so the bytes are pure ASCII.
    assert "café" not in raw
    assert "caf\\u00e9" in raw
    raw.encode("ascii")  # raises if any non-ASCII survived

    # And the routing headers a relay may rewrite are excluded from the signature.
    assert "dst" not in payload and "ttl" not in payload and "sig" not in payload
