"""The Atlas-backed presence directory.

The friends roster is local and authoritative (see `store.py`). This module answers
only one question, and it is deliberately the *smallest* question that still makes
friending work across the internet:

    given a person id, what addresses is that person reachable at right now?

Nothing about who your friends are is ever written here. That keeps the social
graph on your own machine, and means the cluster going away downgrades discovery
to "you need their address once" rather than breaking the roster.

Records are **self-certifying and self-published**: a node publishes its own
person id, public key, and dialable addresses, signed by the person key. A reader
verifies the signature and the `person_id`-is-the-key-fingerprint invariant before
trusting an address, so a compromised or hostile directory can withhold records or
serve stale ones, but cannot point you at an impostor — the peer handshake would
reject them anyway, since node ids are self-certifying too.

Collection: `presence`, keyed by `person_id`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from backend import atlas
from backend.modules.network import ice
from backend.modules.network import identity as node_identity
from backend.modules.network import trust
from backend.modules.social import identity as person_identity

logger = logging.getLogger(__name__)

COLLECTION = "presence"

# How long a published record is considered current. A node republishes on every
# startup and whenever its address changes; anything older than this is treated as
# stale rather than deleted, so a brief outage doesn't erase a person.
TTL_SECONDS = 15 * 60


def _canonical_record(record: dict[str, Any]) -> bytes:
    """The bytes a presence record's signature covers.

    Same discipline as the peer wire and device certificates: pinned key order and
    compact separators, because the signer and the verifier are different machines.
    """
    import json

    payload = {k: v for k, v in record.items() if k not in ("sig", "_id")}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signed_in_handle() -> str | None:
    """This node's game-server username, or None when signed out.

    Imported lazily: the directory must keep working on a node that never signs in
    to the game server, and `server_auth` reaches for settings and the data dir.
    """
    try:
        from backend.modules.games import server_auth

        return server_auth.signed_in_username()
    except Exception:  # noqa: BLE001 — a missing username is not an error
        return None


def build_record(addresses: list[str] | None = None) -> dict[str, Any] | None:
    """This node's presence record, signed by the person key.

    Returns None on a machine that has been *linked* to a person: it cannot sign
    as that person, so it has nothing publishable. Its addresses still reach the
    person through whichever machine holds the key.

    `addresses` defaults to the single advertised address, which is a **LAN** IP —
    fine for finding your own second machine, useless to someone off your network.
    `publish` therefore passes the full ICE candidate list instead.
    """
    if person_identity.is_linked_device():
        return None
    me = person_identity.load_person()
    node = node_identity.load_identity()
    record = {
        "person_id": me.person_id,
        "person_public_key": me.public_key,
        "display_name": person_identity.display_name(),
        # The username, when this machine is signed in to the game server. A handle
        # here is a **claim, not proof**: `verify_record` checks that the record was
        # signed by the person key it names, which says nothing about who owns the
        # username. The game server is the authority — resolve @handle through
        # `handles.resolve`, never by trusting this field.
        "handle": _signed_in_handle(),
        "node_id": node.node_id,
        "addresses": addresses or [trust.advertised_address()],
        "updated_at": time.time(),
    }
    record["sig"] = me.sign(_canonical_record(record))
    return record


def verify_record(record: dict[str, Any]) -> bool:
    """Whether a directory record is authentic and internally consistent."""
    try:
        person_key = str(record["person_public_key"])
        if person_identity.fingerprint(person_key) != str(record["person_id"]):
            return False
        return node_identity.verify(
            person_key, _canonical_record(record), str(record["sig"])
        )
    except Exception:
        return False


async def publish() -> bool:
    """Announce where this node can be reached. False when Atlas is unavailable.

    Never raises: publishing is best-effort, and a node with no directory is a
    node that can still be friended by address on a LAN.
    """
    collection = atlas.collection(COLLECTION)
    if collection is None:
        return False
    # Publish the full ICE candidate list — LAN host candidates first, then the
    # STUN server-reflexive (public) one when `network.iceEnabled` is on. Without
    # that public candidate a record only ever helps someone on the same network,
    # which defeats the point of having a directory at all.
    try:
        candidates = await ice.gather_candidates()
    except Exception:
        candidates = []
    record = build_record(candidates)
    if record is None:
        return False
    try:
        await collection.update_one(
            {"person_id": record["person_id"]}, {"$set": record}, upsert=True
        )
    except Exception as exc:
        logger.info("presence publish failed: %s", exc)
        return False
    return True


async def lookup(person_id: str) -> list[str]:
    """Addresses `person_id` is currently reachable at, best-effort.

    Returns an empty list when Atlas is unavailable, the person has never
    published, or the record fails verification — every caller already falls back
    to a user-supplied address.
    """
    collection = atlas.collection(COLLECTION)
    if collection is None:
        return []
    try:
        record = await collection.find_one({"person_id": person_id})
    except Exception as exc:
        logger.info("presence lookup failed: %s", exc)
        return []
    if not record or not verify_record(record):
        return []
    if time.time() - float(record.get("updated_at", 0)) > TTL_SECONDS:
        return []
    addresses = record.get("addresses") or []
    return [str(a) for a in addresses if a]


async def unpublish() -> None:
    """Withdraw this node's record, best-effort (called on clean shutdown)."""
    collection = atlas.collection(COLLECTION)
    if collection is None or person_identity.is_linked_device():
        return
    try:
        await collection.delete_one(
            {"person_id": person_identity.load_person().person_id}
        )
    except Exception:
        pass
