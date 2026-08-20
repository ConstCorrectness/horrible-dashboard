"""Trust policy + known-peer persistence.

Slice 1 ships the minimal surface the hub needs: evaluate whether to admit a peer
given the configured trust mode and any pairing token. Invite-link generation and
the full known-peers store land in a later slice; the seams here don't change.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import socket
import time
from pathlib import Path
from typing import Any

from backend.modules.settings.routes import get_value
from backend import jsonstore, paths

logger = logging.getLogger(__name__)

INVITE_TTL_S = 24 * 3600

# Trust modes (setting `network.trustMode`):
#   manual    — a peer must present a valid, unredeemed invite token to pair
#   open-lan  — accept any peer reachable (demo / trusted LAN only)
#
# `directory` was a third mode. Nothing implemented it, so it rejected every peer
# and made the node unpairable; it is no longer offered, and `trust_mode`
# normalizes a stored one to `manual`. The constant stays only for that mapping.
TRUST_MANUAL = "manual"
TRUST_DIRECTORY = "directory"
TRUST_OPEN_LAN = "open-lan"


def _data_dir() -> Path:
    return paths.data_dir()


def _peers_path() -> Path:
    return _data_dir() / "network-peers.json"


def _invites_path() -> Path:
    return _data_dir() / "network-invites.json"


def _load_invites() -> dict[str, dict[str, Any]]:
    text = jsonstore.read_text(_invites_path())
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_invites(data: dict[str, dict[str, Any]]) -> None:
    jsonstore.write_text(_invites_path(), json.dumps(data))


@jsonstore.serialized(_invites_path)
def make_invite(address: str, node_id: str) -> tuple[str, str, float]:
    """Mint a single-use pairing token and pack it (with how to reach this node)
    into an opaque invite string. Returns (invite, token, expires)."""
    token = secrets.token_urlsafe(18)
    expires = time.time() + INVITE_TTL_S
    invites = _load_invites()
    invites[token] = {"created": time.time(), "expires": expires, "redeemed_by": None}
    _save_invites(invites)
    bundle = {"address": address, "token": token, "node_id": node_id}
    raw = base64.urlsafe_b64encode(json.dumps(bundle).encode("utf-8")).decode("ascii")
    return raw, token, expires


def parse_invite(invite: str) -> tuple[str, str]:
    """Decode an invite into (address, token). Raises on malformed input."""
    bundle = json.loads(base64.urlsafe_b64decode(invite.encode("ascii")))
    return str(bundle["address"]), str(bundle["token"])


@jsonstore.serialized(_invites_path)
def redeem_token(token: str, node_id: str) -> bool:
    """Consume a pairing token for `node_id`. Returns True if the token was valid and
    unredeemed (or already redeemed by this same node).

    Serialized because this one is not merely a lost update: the token is
    **single-use**, and check-then-write over a shared file means two nodes
    redeeming the same invite at the same time both read `redeemed_by: None`, both
    write themselves in, and both are told yes. An invite you handed to one person
    would have paired two.
    """
    invites = _load_invites()
    rec = invites.get(token)
    if rec is None or rec.get("expires", 0) < time.time():
        return False
    if rec.get("redeemed_by") not in (None, node_id):
        return False
    rec["redeemed_by"] = node_id
    _save_invites(invites)
    return True


def load_known_peers() -> dict[str, dict[str, Any]]:
    text = jsonstore.read_text(_peers_path())
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@jsonstore.serialized(_peers_path)
def save_known_peer(node_id: str, record: dict[str, Any]) -> None:
    peers = load_known_peers()
    peers[node_id] = {**peers.get(node_id, {}), **record}
    jsonstore.write_text(_peers_path(), json.dumps(peers))


def is_blocked(node_id: str) -> bool:
    return bool(load_known_peers().get(node_id, {}).get("blocked"))


def is_trusted(node_id: str) -> bool:
    return bool(load_known_peers().get(node_id, {}).get("trusted"))


def trust_mode() -> str:
    """The pairing policy, with the one dead value normalized away.

    `directory` was offered as a third mode and nothing ever implemented it, so
    `evaluate` rejected every peer — choosing it made the node quietly unpairable.
    It is gone from the settings enum, but a node that stored it must not stay
    broken, so it is read as `manual`.
    """
    mode = str(get_value("network.trustMode", TRUST_MANUAL))
    if mode == TRUST_DIRECTORY:
        logger.warning(
            "network.trustMode was %r, which was never implemented — using %r",
            TRUST_DIRECTORY,
            TRUST_MANUAL,
        )
        return TRUST_MANUAL
    return mode


def lan_ip() -> str | None:
    """This machine's LAN IPv4, or None if it can't be determined.

    Opens a UDP socket toward a public address and reads back the local end the
    OS picked — no packets are sent, and it beats `gethostbyname(gethostname())`,
    which on Windows routinely answers `127.0.0.1` or a stale adapter.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = str(sock.getsockname()[0])
        finally:
            sock.close()
    except Exception:
        return None
    return ip if ip and not ip.startswith("127.") else None


def advertised_address() -> str:
    """The `ws://…/peer-ws` URL peers should dial to reach this node.

    Defaults to this machine's **LAN IP**, not `localhost`: the address is baked
    into every invite QR code, and a phone scanning `ws://localhost:…` dials
    itself. An explicit `network.advertisedAddress` setting still wins (needed
    for a public hostname or a port-forwarded box); blank means auto-detect.
    """
    port = os.environ.get("HORRIBLE_DEV_BACKEND_PORT", "8000")
    default = f"ws://{lan_ip() or 'localhost'}:{port}/peer-ws"
    return str(get_value("network.advertisedAddress", "") or default)


def evaluate(node_id: str, token: str | None) -> tuple[bool, str | None]:
    """Decide whether to admit a peer. Returns (ok, reason_if_rejected).

    A peer already trusted (paired earlier) is always admitted unless blocked. A new
    peer is admitted per the trust mode: a valid invite token (manual), or open-lan.
    The directory path is stubbed to reject until that service exists.
    """
    if is_blocked(node_id):
        return False, "blocked"
    if is_trusted(node_id):
        return True, None

    mode = trust_mode()
    if mode == TRUST_OPEN_LAN:
        save_known_peer(node_id, {"trusted": True, "via": "open-lan"})
        return True, None
    if mode == TRUST_MANUAL:
        if token and redeem_token(token, node_id):
            save_known_peer(node_id, {"trusted": True, "via": "token"})
            return True, None
        return False, "pairing required"
    return False, "untrusted"
