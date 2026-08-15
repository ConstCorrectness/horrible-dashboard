"""Stable node identity: an Ed25519 keypair persisted under `$HORRIBLE_DATA_DIR`.

The node id is `base32(sha256(pubkey))[:16]` — self-certifying, so a peer presenting
a public key whose fingerprint doesn't match the node id it claims is rejected during
the handshake. The private key is written `0600` and never leaves the process or
crosses any API boundary. Mirrors clubhouse's `_device_id()` install-stable-id model.
"""

from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.modules.network.models import NodeId
from backend.modules.settings.routes import get_value
from backend import paths


def _data_dir() -> Path:
    return paths.data_dir()


def _identity_path() -> Path:
    return _data_dir() / "network-identity.json"


def _key_path() -> Path:
    return _data_dir() / "network-identity.key"


def fingerprint(public_key_b64: str) -> NodeId:
    """The node id derived from a base64 raw Ed25519 public key."""
    raw = base64.b64decode(public_key_b64)
    digest = hashlib.sha256(raw).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()[:16]


def encode_public(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def decode_public(public_key_b64: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))


class Identity:
    """This node's keypair plus its derived id. Loaded once per process."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private = private_key
        self.public_key = encode_public(private_key.public_key())
        self.node_id = fingerprint(self.public_key)

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self._private.sign(payload)).decode("ascii")


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
def _cached_identity(key_path: str) -> Identity:
    # Keyed by path so a test swapping HORRIBLE_DATA_DIR gets a fresh identity.
    return Identity(_load_or_create_private())


def load_identity() -> Identity:
    """This node's identity (lazily generated and persisted on first call)."""
    return _cached_identity(str(_key_path()))


def node_name() -> str:
    """Display name advertised to peers — a setting, defaulting to the hostname."""
    import socket

    default = os.environ.get("HOSTNAME") or socket.gethostname()
    value = get_value("network.nodeName", default)
    return str(value).strip() or default


def verify(public_key_b64: str, payload: bytes, signature_b64: str) -> bool:
    """Whether `signature_b64` is a valid Ed25519 signature over `payload` by the
    holder of `public_key_b64`. Never raises — a malformed key/sig returns False."""
    try:
        key = decode_public(public_key_b64)
        key.verify(base64.b64decode(signature_b64), payload)
        return True
    except Exception:
        return False
