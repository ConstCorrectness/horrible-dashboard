"""`@handle` — one name for a person, across the ladder and the friends roster.

Before this, a human had **two** unrelated identities and no way to connect them:
a globally unique `handle` on the game server (what the ladder and HorribleAssault
display), and a `person_id` on the peer fabric (rendered as an `HD-XXXX-…` friend
code). One screen asked for an "Account ID" while the one next to it asked for a
friend code, and neither could answer "who is this?" about the other's answer.

The game server is the only uniqueness authority every node agrees on, so the
mapping lives there (`accounts.person_id`, unique both ways) and this module is the
node's client for it:

- `publish_binding()` proves to the game server that this person owns this account,
- `resolve('@rob')` turns a callsign into something `roster.add_friend` can dial,
- `search('ro')` is the "easier way to find people".

**What a handle is and isn't.** It is a *directory* name: proof that some account
claims this person key, vouched for by the game server. It is not an authority on
the fabric — reaching someone still means their signed presence record and the
usual device-certificate checks. A hostile game server could point `@rob` at the
wrong person key, which is exactly why the friend code (self-certifying, derived
from the key) remains the offline path and stays first-class. See
docs/modules/social.mdx.
"""

from __future__ import annotations

import logging
import re
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
    """Whether `value` looks like `@callsign`.

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


async def publish_binding() -> dict[str, Any]:
    """Tell the game server which person this signed-in account is.

    Called after sign-in and whenever the person key is available. Idempotent, so
    running it on every sign-in costs one request and nothing else.

    A **linked device** cannot do this: it holds no person private key, so it
    cannot sign the challenge. That is correct rather than a limitation — the
    binding is a statement about a person, and only the machine holding the key
    can make one. The link is already published by whichever machine holds it.
    """
    token = server_auth.get_token()
    if not token:
        return {"error": "sign in to the game server first"}
    if person_identity.is_linked_device():
        return {"error": "this machine is linked to a person; bind from the primary"}

    account = server_auth.signed_in_account() or {}
    account_id = str(account.get("id") or "")
    if not account_id:
        return {"error": "no account id on the stored session"}

    me = person_identity.load_person()
    # The challenge must match the server's `store.person_challenge` byte for byte —
    # canonical JSON, sorted keys, compact separators, and the account id inside it
    # so the signature cannot be replayed onto a different account.
    import json

    challenge = json.dumps(
        {
            "purpose": "horrible.account.person",
            "account_id": account_id,
            "person_id": me.person_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                f"{_base()}/account/person",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "person_id": me.person_id,
                    "person_public_key": me.public_key,
                    "sig": me.sign(challenge),
                },
            )
            return dict(res.json())
    except Exception as exc:  # noqa: BLE001 — best effort, never fatal
        logger.debug("person binding failed: %s", exc)
        return {"error": f"game server unreachable: {exc}"}


async def resolve(handle: str) -> dict[str, Any] | None:
    """`@rob` → `{handle, display_name, person_id, person_public_key}`, or None.

    Unauthenticated: a callsign is already public, and needing an account to look
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
    # fingerprint of the key it came with, or the server could point a callsign at
    # a key that isn't theirs. This check is what keeps a handle lookup no weaker
    # than a friend code.
    if person_identity.fingerprint(str(entry["person_public_key"])) != str(
        entry["person_id"]
    ):
        logger.warning("directory entry for @%s is inconsistent — ignoring", name)
        return None
    return entry


async def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Prefix-search callsigns. Short queries return nothing, by server policy."""
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
