"""`@handle` — one name for a person, across the ladder and the friends roster.

Before this, a human had **two** unrelated identities and no way to connect them:
a globally unique `handle` on the game server (what the ladder and HorribleAssault
display), and a `person_id` on the peer fabric (rendered as an `HD-XXXX-…` friend
code). One screen asked for an "Account ID" while the one next to it asked for a
friend code, and neither could answer "who is this?" about the other's answer.

The game server is the only uniqueness authority every node agrees on, so the
**account is the identity**: the server holds each account's person key, and this
module is the node's client for it:

- `enroll_device()` makes this machine one of the signed-in account's, adopting the
  certificate the server signs for it,
- `resolve('@rob')` turns a username into something `roster.add_friend` can dial,
- `search('ro')` is the "easier way to find people".

**What this trusts.** A friend's machines are still checked against the person key
offline, but that key is the server's to vouch for: whoever runs the game server
can enroll a machine under any account. That is the price of "sign in anywhere and
you are you", chosen deliberately over a key only your own machines hold. See
docs/modules/social.mdx.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from backend.modules.games import server_auth
from backend.modules.games.client import resolve_server_url
from backend.modules.social import identity as person_identity

logger = logging.getLogger(__name__)

#: Same shape the game server enforces (`store.HANDLE_RE`), minus the leading `@`.
HANDLE_RE = re.compile(r"^[a-z0-9_-]{3,20}$")

_TIMEOUT = 8.0


def is_handle(value: str) -> bool:
    """Whether `value` looks like `@username`.

    The `@` is **required**. Without it a bare `rob` would be ambiguous against a
    display name, and guessing between them is how an agent messages the wrong
    person — the same reason `_resolve` refuses an ambiguous display name.
    """
    value = value.strip()
    return value.startswith("@") and bool(HANDLE_RE.match(value[1:].lower()))


def normalize(value: str) -> str:
    """`@Rob` → `rob`. Assumes `is_handle` already passed."""
    return value.strip().lstrip("@").lower()


def _base() -> str:
    url = resolve_server_url()
    return url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


def enroll_challenge(account_id: str, node_id: str, ts: float) -> bytes:
    """What this machine signs with its node key to enroll.

    Must match the game server's `store.enroll_challenge` byte for byte;
    `test_games_account_identity.py` pins the two together.
    """
    payload = {
        "purpose": "horrible.account.device",
        "account_id": account_id,
        "node_id": node_id,
        "ts": ts,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


async def enroll_device() -> dict[str, Any]:
    """Become one of the signed-in account's machines.

    The server signs this machine's certificate with the account's person key, and
    it is adopted as this machine's identity (`identity.adopt_cert`). Its other
    machines come back too and are recorded as ours, reachable over the relay.
    Idempotent, so it runs on every sign-in and startup.
    """
    from backend.modules.network import identity as node_identity

    token = server_auth.get_token()
    account = server_auth.signed_in_account() or {}
    account_id = str(account.get("id") or "")
    if not token or not account_id:
        return {"error": "sign in to the game server first"}
    node = node_identity.load_identity()
    ts = time.time()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                f"{_base()}/account/devices",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "node_id": node.node_id,
                    "node_public_key": node.public_key,
                    "ts": ts,
                    "sig": node.sign(enroll_challenge(account_id, node.node_id, ts)),
                },
            )
            data = dict(res.json())
    except Exception as exc:  # noqa: BLE001 — best effort, never fatal
        logger.debug("device enrollment failed: %s", exc)
        return {"error": f"game server unreachable: {exc}"}
    cert = data.get("cert")
    if data.get("error") or not isinstance(cert, dict):
        return {"error": str(data.get("error") or "no certificate in the reply")}
    # Believe nothing the server says about us that does not verify, and nothing
    # addressed to a different machine.
    if (
        not person_identity.verify_device_cert(cert)
        or str(cert.get("node_id")) != node.node_id
        or str(cert.get("node_public_key")) != node.public_key
    ):
        return {"error": "the game server returned a certificate for another machine"}
    person_identity.adopt_cert(cert)
    entry = {
        "person_id": cert["person_id"],
        "person_public_key": cert["person_public_key"],
        "devices": data.get("devices") or [],
    }
    others = verified_devices(entry)
    from backend.modules.social import store

    store.upsert_device(
        node_id=node.node_id,
        person_id=str(cert["person_id"]),
        node_public_key=node.public_key,
        label=node_identity.node_name(),
        cert=cert,
    )
    for other in others:
        store.upsert_device(
            node_id=str(other["node_id"]),
            person_id=str(other["person_id"]),
            node_public_key=str(other["node_public_key"]),
            label=str(other.get("label") or other["node_id"]),
            cert=other,
            address=f"relay:{other['node_id']}",
        )
    return {"ok": True, "person_id": cert["person_id"], "devices": len(others)}


async def unenroll_device() -> None:
    """Unlist this machine from its account and drop the adopted identity.

    Called on sign-out, **before** the token is deleted (the request needs it). The
    local drop happens whether or not the server answers: a signed-out machine is
    nobody's, even if the directory still lists it until the next enrollment.
    """
    from backend.modules.network import identity as node_identity

    token = server_auth.get_token()
    if token:
        node_id = node_identity.load_identity().node_id
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                await client.delete(
                    f"{_base()}/account/devices/{node_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("device unenrollment failed: %s", exc)
    person_identity.drop_adopted_cert()


def verified_devices(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The device certificates in a directory entry that are really that person's.

    The directory is not trusted to have told the truth: each certificate must
    verify under the entry's person key, which must itself be the one whose
    fingerprint is the person id — the same rule `resolve` applies to the entry.
    """
    person_id = str(entry.get("person_id") or "")
    person_key = str(entry.get("person_public_key") or "")
    out = []
    for device in entry.get("devices") or []:
        cert = device.get("cert") if isinstance(device, dict) else None
        if not isinstance(cert, dict):
            continue
        if str(cert.get("person_id")) != person_id:
            continue
        if str(cert.get("person_public_key")) != person_key:
            continue
        if not person_identity.verify_device_cert(cert):
            continue
        out.append(cert)
    return out


async def resolve(handle: str) -> dict[str, Any] | None:
    """`@rob` → `{handle, display_name, person_id, person_public_key}`, or None.

    Unauthenticated: a username is already public, and needing an account to look
    one up would lock out exactly the person trying to find you.
    """
    name = normalize(handle)
    if not HANDLE_RE.match(name):
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.get(
                f"{_base()}/directory/resolve", params={"handle": name}
            )
            data = res.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("handle resolve failed: %s", exc)
        return None
    entry = data.get("entry") if isinstance(data, dict) else None
    if not isinstance(entry, dict) or not entry.get("person_id"):
        return None
    # Never trust the directory's arithmetic: the person id must actually be the
    # fingerprint of the key it came with, or the server could point a username at
    # a key that isn't theirs. This check is what keeps a handle lookup no weaker
    # than a friend code.
    if person_identity.fingerprint(str(entry["person_public_key"])) != str(
        entry["person_id"]
    ):
        logger.warning("directory entry for @%s is inconsistent — ignoring", name)
        return None
    return entry


async def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Prefix-search usernames. Short queries return nothing, by server policy."""
    q = query.strip().lstrip("@").lower()
    if len(q) < 3:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.get(
                f"{_base()}/directory/search", params={"q": q, "limit": limit}
            )
            data = res.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("handle search failed: %s", exc)
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    return [
        r
        for r in results
        if isinstance(r, dict)
        and r.get("person_id")
        and person_identity.fingerprint(str(r.get("person_public_key") or ""))
        == str(r["person_id"])
    ]
