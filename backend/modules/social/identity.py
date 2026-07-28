"""Person identity: the durable "who you are" that spans your machines.

The peer fabric's `node_id` names a *machine*. People routinely run several — a
desktop, a laptop, a phone — so friending at the node level would make one human
show up in the roster three times, and would make "add my own laptop as a friend"
a thing users have to do. This module adds a second, longer-lived Ed25519 keypair
(the **person key**) and derives a `person_id` from it exactly the way the fabric
derives a node id: `base32(sha256(pubkey))[:16]`, self-certifying.

A machine proves it belongs to a person by presenting a **device certificate** —
the person key's signature over `(person_id, node_id, node_public_key, label)`.
A peer verifies one offline:

1. `person_id` is the fingerprint of the presented person public key,
2. the signature is valid under that key, and
3. `node_id` is the node it is *actually* talking to (checked by the caller, which
   is the only party that knows which session the cert arrived on).

Step 3 is what stops a leaked certificate from being replayed by a different
machine: the cert names one node, and the fabric already authenticates node ids
self-certifyingly during the handshake.

Only the machine holding the person private key can mint certificates. Linking a
second machine therefore means that machine sends its node identity to the primary
and gets a cert back — see `roster.link_device`. This slice has **no revocation**:
a certificate is valid until the person key is rotated, which invalidates every
cert at once. See docs/modules/social.mdx.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.modules.network import identity as node_identity
from backend.modules.social.friendcode import format_friend_code

# A person id is the same shape as a node id, and for the same reason: it is the
# fingerprint of a public key, so it cannot be claimed by anyone else.
PersonId = str


def _data_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))


def _key_path() -> Path:
    return _data_dir() / "social-person.key"


def _profile_path() -> Path:
    return _data_dir() / "social-person.json"


def fingerprint(public_key_b64: str) -> PersonId:
    """The person id derived from a base64 raw Ed25519 public key."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()[:16]


def canonical_cert_bytes(cert: dict[str, Any]) -> bytes:
    """The bytes a device certificate's signature covers.

    Pinned here rather than left to dict ordering because signer and verifier are
    different machines — and, once the Android companion grows a person identity,
    different languages. Same discipline as `network.protocol.canonical_bytes`.
    """
    payload = {k: v for k, v in cert.items() if k != "sig"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class PersonIdentity:
    """This user's person keypair, plus the derived id and friend code."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private = private_key
        raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key = base64.b64encode(raw).decode("ascii")
        self.person_id = fingerprint(self.public_key)
        self.friend_code = format_friend_code(self.person_id)

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self._private.sign(payload)).decode("ascii")

    def issue_device_cert(
        self, node_id: str, node_public_key: str, label: str
    ) -> dict[str, Any]:
        """Mint a certificate binding one machine to this person."""
        cert = {
            "person_id": self.person_id,
            "person_public_key": self.public_key,
            "node_id": node_id,
            "node_public_key": node_public_key,
            "label": label,
            "issued_at": time.time(),
        }
        cert["sig"] = self.sign(canonical_cert_bytes(cert))
        return cert


def _load_or_create_private() -> Ed25519PrivateKey:
    path = _key_path()
    if path.is_file():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)  # type: ignore[return-value]
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pem)
    os.chmod(path, 0o600)
    return private


@lru_cache(maxsize=1)
def _cached_identity(key_path: str) -> PersonIdentity:
    # Keyed by path so a test pointing HORRIBLE_DATA_DIR elsewhere gets a fresh
    # person rather than the developer's real one.
    return PersonIdentity(_load_or_create_private())


def load_person() -> PersonIdentity:
    """This user's person identity (generated and persisted on first call)."""
    return _cached_identity(str(_key_path()))


def has_person_key() -> bool:
    """Whether this machine holds the person private key.

    False on a machine that was linked to a person by another device: it carries a
    certificate but cannot mint new ones.
    """
    return _key_path().is_file()


def verify_device_cert(cert: dict[str, Any]) -> bool:
    """Whether `cert` is internally consistent and correctly signed.

    Does **not** check that the cert describes the node you are talking to — the
    caller holds the session and must compare `cert["node_id"]` itself. Never
    raises; a malformed certificate is simply invalid.
    """
    try:
        person_key = str(cert["person_public_key"])
        if fingerprint(person_key) != str(cert["person_id"]):
            return False
        return node_identity.verify(
            person_key, canonical_cert_bytes(cert), str(cert["sig"])
        )
    except Exception:
        return False


# ---- local profile (display name / avatar shown to friends) ------------------------


def load_profile() -> dict[str, Any]:
    path = _profile_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_profile(**fields: Any) -> dict[str, Any]:
    profile = {**load_profile(), **{k: v for k, v in fields.items() if v is not None}}
    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile), encoding="utf-8")
    return profile


def display_name() -> str:
    """The name friends see. Falls back to this node's advertised name."""
    name = str(load_profile().get("display_name", "")).strip()
    return name or node_identity.node_name()


def adopted_cert() -> dict[str, Any] | None:
    """The certificate another of your machines minted for this one, if any.

    Present exactly when this machine has been *claimed* — linked by the device
    holding the person key. Re-verified on every read so a corrupted or tampered
    profile file falls back to this machine's own identity rather than serving a
    bad certificate.
    """
    cached = load_profile().get("device_cert")
    if isinstance(cached, dict) and verify_device_cert(cached):
        return cached
    return None


def is_linked_device() -> bool:
    """Whether this machine belongs to a person whose key lives elsewhere.

    The predicate that actually matters for capability, and deliberately *not*
    `has_person_key()`: a machine generates its own person key the moment anything
    asks who it is, so holding one says nothing about whether it has since been
    claimed by another device.
    """
    return adopted_cert() is not None


def self_cert() -> dict[str, Any]:
    """This machine's device certificate.

    An adopted certificate wins when there is one — that is what makes a linked
    machine present as its owner rather than as a separate person. Otherwise the
    machine is its own primary and self-issues, generating a person key on first
    call if it doesn't have one yet.
    """
    adopted = adopted_cert()
    if adopted is not None:
        return adopted
    me = node_identity.load_identity()
    return load_person().issue_device_cert(
        me.node_id, me.public_key, node_identity.node_name()
    )


def effective_person_id() -> PersonId:
    """The person this machine acts as — its owner's id when linked, else its own."""
    adopted = adopted_cert()
    return str(adopted["person_id"]) if adopted else load_person().person_id
