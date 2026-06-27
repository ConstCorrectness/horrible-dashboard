"""Trust policy + known-peer persistence.

Slice 1 ships the minimal surface the hub needs: evaluate whether to admit a peer
given the configured trust mode and any pairing token. Invite-link generation and
the full known-peers store land in a later slice; the seams here don't change.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from backend.modules.settings.routes import get_value

INVITE_TTL_S = 24 * 3600

# Trust modes (setting `network.trustMode`):
#   manual    — a peer must present a valid, unredeemed invite token to pair
#   directory — identities vouched for by the configured directory service
#   open-lan  — accept any peer reachable (demo / trusted LAN only)
TRUST_MANUAL = "manual"
TRUST_DIRECTORY = "directory"
TRUST_OPEN_LAN = "open-lan"


def _data_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))


def _peers_path() -> Path:
    return _data_dir() / "network-peers.json"


def _invites_path() -> Path:
    return _data_dir() / "network-invites.json"


def _load_invites() -> dict[str, dict[str, Any]]:
    path = _invites_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_invites(data: dict[str, dict[str, Any]]) -> None:
    path = _invites_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


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


def redeem_token(token: str, node_id: str) -> bool:
    """Consume a pairing token for `node_id`. Returns True if the token was valid and
    unredeemed (or already redeemed by this same node)."""
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
    path = _peers_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_known_peer(node_id: str, record: dict[str, Any]) -> None:
    peers = load_known_peers()
    peers[node_id] = {**peers.get(node_id, {}), **record}
    path = _peers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(peers), encoding="utf-8")


def is_blocked(node_id: str) -> bool:
    return bool(load_known_peers().get(node_id, {}).get("blocked"))


def is_trusted(node_id: str) -> bool:
    return bool(load_known_peers().get(node_id, {}).get("trusted"))


def trust_mode() -> str:
    return str(get_value("network.trustMode", TRUST_MANUAL))


def advertised_address() -> str:
    """The `ws://…/peer-ws` URL peers should dial to reach this node. The external
    host can't be inferred reliably, so it's a setting (default localhost:8000)."""
    return str(get_value("network.advertisedAddress", "ws://localhost:8000/peer-ws"))


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
    if mode == TRUST_DIRECTORY:
        return False, "directory service not configured"
    return False, "untrusted"
