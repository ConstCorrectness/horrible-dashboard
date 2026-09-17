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

import asyncio
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

# How long a published record is considered current. Anything older is treated as
# stale rather than deleted, so a brief outage doesn't erase a person.
TTL_SECONDS = 15 * 60

#: How often a running node republishes. A third of the TTL, so one missed refresh
#: (Atlas briefly unreachable) still leaves a current record. This used to be never:
#: the record was published once at startup, so any node up for more than fifteen
#: minutes was invisible to `lookup`, and friend reconnects through the directory
#: failed without a word.
REFRESH_SECONDS = TTL_SECONDS / 3

#: How often the loop checks whether this node's local addresses changed (a laptop
#: moving networks, Tailscale coming up). Cheap: host candidates only, no STUN.
CHANGE_CHECK_SECONDS = 60.0

#: The candidate list last written, and the host candidates it was built from — the
#: change check compares against the latter so it never needs a STUN round trip.
_last_published: list[str] | None = None
_last_hosts: list[str] | None = None
_refresh_task: asyncio.Task[None] | None = None


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


def relay_candidates() -> list[str]:
    """`relay:<node_id>` when this node runs the relay transport.

    Every other candidate is an address, and every address is only reachable from
    somewhere: a LAN IP from that LAN, a Tailscale IP from that tailnet. The relay
    candidate is the one a friend anywhere can use, which is what makes adding
    someone by username work between two homes. It names this node, not a server,
    so the reader dials it through whichever relay *they* are registered with.
    """
    from backend.modules.network.hub import peer_hub

    if not any(t.name == "relay" for t in peer_hub.transports):
        return []
    return [f"relay:{node_identity.load_identity().node_id}"]


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
    global _last_published, _last_hosts
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
    candidates += relay_candidates()
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
    _last_published = list(record["addresses"])
    _last_hosts = _host_candidates()
    return True


def _host_candidates() -> list[str] | None:
    try:
        return ice.host_candidates()
    except Exception:  # noqa: BLE001 - a failed probe is "unknown", not "changed"
        return None


async def refresh_loop(
    *,
    refresh_seconds: float = REFRESH_SECONDS,
    check_seconds: float = CHANGE_CHECK_SECONDS,
) -> None:
    """Keep this node's record current for as long as the node runs.

    Republishes every `refresh_seconds`, and early when the local addresses change —
    a record naming the network you just left is as useless as a stale one. A
    publish that fails is retried on the next check rather than waiting a full
    refresh, with no bookkeeping for it: nothing that made the round due (`last_ok`,
    `_last_hosts`, `pending`) moves until a publish succeeds. And nothing here
    raises: one bad round must not end the loop, because a dead loop is the original
    bug again, only later.
    """
    last_ok = time.monotonic()
    # If the startup publish did not land, retry on the first check rather than
    # leaving the node unfindable for a whole refresh interval.
    pending = _last_published is None
    while True:
        await asyncio.sleep(check_seconds)
        try:
            hosts = _host_candidates()
            changed = (
                hosts is not None and _last_hosts is not None and hosts != _last_hosts
            )
            due = pending or time.monotonic() - last_ok >= refresh_seconds
            if not (changed or due):
                continue
            if changed:
                logger.info("local addresses changed; republishing presence")
            if await publish():
                last_ok = time.monotonic()
                pending = False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("presence refresh round failed", exc_info=True)


def start_refresh() -> bool:
    """Start the refresh loop, once. False when there is nothing to publish to.

    Atlas is configured from the environment, which cannot change while the process
    runs, so an unconfigured node never starts the loop at all.
    """
    global _refresh_task
    if not atlas.is_configured():
        return False
    if _refresh_task is None or _refresh_task.done():
        _refresh_task = asyncio.create_task(refresh_loop())
    return True


async def stop_refresh() -> None:
    global _refresh_task
    task, _refresh_task = _refresh_task, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


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
    """Withdraw this node's record, best-effort.

    **Not** called on shutdown, deliberately. Records are keyed by person, so
    deleting on every stop would make you unfindable across a restart — and under
    `--reload` a restart is every save. A node that stops simply stops refreshing,
    and its record goes stale after `TTL_SECONDS`; that is the withdrawal, and it
    also covers the crash no shutdown hook would see.
    """
    collection = atlas.collection(COLLECTION)
    if collection is None or person_identity.is_linked_device():
        return
    try:
        await collection.delete_one(
            {"person_id": person_identity.load_person().person_id}
        )
    except Exception:
        pass
