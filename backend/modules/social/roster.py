"""The friendship state machine, and its bridge onto the peer fabric.

Four message types ride the existing signed peer wire — declared here rather than
in `network/protocol.py` so the social module extends the fabric without the
fabric having to know it exists, the same way `training/fabric.py` contributes
`training_ad`.

    social_hello            who I am: my device certificate + display name
    social_friend_request   please add me
    social_friend_response  accepted / declined
    social_device_cert      a certificate minted for a machine being linked

The one design point worth stating plainly: **accepting a friend grants fabric
trust**. Every device of an accepted friend is written into the network module's
known-peers store as trusted, which is precisely what makes peer chat, shared
panes, and agent-to-agent questions work between friends with no second pairing
step. Removing or blocking a friend revokes it again.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from backend.modules.network import identity as node_identity
from backend.modules.network import trust
from backend.modules.network.hub import peer_hub
from backend.modules.social import identity as person_identity
from backend.modules.social import directory, store
from backend.modules.social.friendcode import format_friend_code, resolve_person_id
from backend.modules.social.models import (
    Friend,
    RosterSnapshot,
    SelfProfile,
)

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

SOCIAL_HELLO = "social_hello"
SOCIAL_FRIEND_REQUEST = "social_friend_request"
SOCIAL_FRIEND_RESPONSE = "social_friend_response"
SOCIAL_DEVICE_CERT = "social_device_cert"

# Callbacks the `/ws` social channel registers to push roster updates to browsers.
_subscribers: set[Any] = set()


def subscribe(cb: Any) -> Any:
    _subscribers.add(cb)
    return lambda: _subscribers.discard(cb)


def _emit(event: str, data: dict[str, Any]) -> None:
    for cb in list(_subscribers):
        try:
            cb(event, data)
        except Exception:
            logger.exception("social subscriber failed")


def broadcast_roster() -> None:
    _emit("roster", snapshot().model_dump())


# ---- snapshot ---------------------------------------------------------------------


def online_nodes() -> set[str]:
    """Node ids with a live session right now — the raw material for presence."""
    return set(peer_hub.peers.keys())


def self_profile() -> SelfProfile:
    """Who this machine acts as.

    On a linked machine that is its *owner* — the friend code shown must be the one
    friends can actually use to reach the person, not this box's unused local key.
    """
    cert = person_identity.self_cert()
    person_id = str(cert["person_id"])
    online = online_nodes()
    return SelfProfile(
        person_id=person_id,
        friend_code=format_friend_code(person_id),
        display_name=person_identity.display_name(),
        person_public_key=str(cert["person_public_key"]),
        holds_person_key=not person_identity.is_linked_device(),
        # Assembled through the same helper the roster uses, so "online" means the
        # same thing for your own machines as for a friend's.
        devices=[store.device_info(d, online) for d in store.list_devices(person_id)],
    )


def snapshot() -> RosterSnapshot:
    return RosterSnapshot(
        self_profile=self_profile(), friends=store.list_friends(online_nodes())
    )


# ---- trust wiring -----------------------------------------------------------------


def _grant_trust(person_id: str) -> None:
    """Mark every known device of `person_id` as a trusted peer."""
    for device in store.list_devices(person_id):
        trust.save_known_peer(
            device["node_id"], {"trusted": True, "via": "friend", "blocked": False}
        )


def _revoke_trust(person_id: str, *, blocked: bool = False) -> None:
    for device in store.list_devices(person_id):
        trust.save_known_peer(device["node_id"], {"trusted": False, "blocked": blocked})


# ---- outbound: identifying ourselves ----------------------------------------------


def _hello_payload() -> dict[str, Any]:
    return {
        "cert": person_identity.self_cert(),
        "display_name": person_identity.display_name(),
        "person_id": person_identity.effective_person_id(),
    }


async def say_hello(node_id: str) -> None:
    """Tell a freshly connected peer who we are.

    Sent on every connection, friend or not: it is how both sides learn which
    person a machine belongs to, which is a prerequisite for the roster showing a
    friend as online rather than showing three anonymous nodes.
    """
    try:
        await peer_hub.send_to(node_id, SOCIAL_HELLO, _hello_payload())
    except KeyError:
        pass
    except Exception:
        logger.exception("social hello to %s failed", node_id)


# ---- inbound handlers -------------------------------------------------------------


def _accept_cert(env: PeerEnvelope, cert: Any, display_name: str) -> str | None:
    """Validate a device certificate that arrived from `env.src` and record it.

    Returns the person id it binds to, or None if the certificate is missing,
    malformed, or describes a machine other than the one that sent it — the replay
    check `identity.verify_device_cert` deliberately leaves to its caller.
    """
    if not isinstance(cert, dict):
        return None
    if not person_identity.verify_device_cert(cert):
        logger.info("rejected device cert from %s: bad signature", env.src)
        return None
    if str(cert.get("node_id")) != env.src:
        logger.info("rejected device cert from %s: names a different node", env.src)
        return None
    person_id = str(cert["person_id"])
    store.upsert_device(
        node_id=env.src,
        person_id=person_id,
        node_public_key=str(cert["node_public_key"]),
        label=str(cert.get("label") or env.src),
        cert=cert,
    )
    # Keep the display name fresh for people already in the roster, but never
    # create a row here: being told who someone is isn't the same as friending them.
    if store.get_friend_row(person_id) is not None and display_name:
        store.upsert_friend(person_id, display_name=display_name)
    return person_id


async def handle_hello(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    person_id = _accept_cert(
        env, env.data.get("cert"), str(env.data.get("display_name", ""))
    )
    if person_id is None:
        return
    # A device of an existing friend just came online — re-grant trust so a machine
    # added to their account after we friended them is reachable too.
    row = store.get_friend_row(person_id)
    if row is not None and row["status"] == "accepted":
        _grant_trust(person_id)
    broadcast_roster()


async def handle_friend_request(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    person_id = _accept_cert(
        env, env.data.get("cert"), str(env.data.get("display_name", ""))
    )
    if person_id is None:
        return
    display_name = str(env.data.get("display_name") or person_id)
    cert = env.data.get("cert") or {}
    row = store.get_friend_row(person_id)

    if row is not None and row["status"] == "blocked":
        return  # blocked people get silence, not a decline
    if row is not None and row["status"] == "pending_out":
        # We each asked the other independently — that is mutual consent, so skip
        # the prompt and settle it as accepted on both sides.
        store.upsert_friend(person_id, status="accepted", display_name=display_name)
        _grant_trust(person_id)
        await _send_response(person_id, accept=True)
        broadcast_roster()
        return
    if row is not None and row["status"] == "accepted":
        await _send_response(person_id, accept=True)
        return

    store.upsert_friend(
        person_id,
        display_name=display_name,
        person_public_key=str(cert.get("person_public_key", "")),
        status="pending_in",
    )
    _emit("friend_request", {"person_id": person_id, "display_name": display_name})
    broadcast_roster()


async def handle_friend_response(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    person_id = _accept_cert(
        env, env.data.get("cert"), str(env.data.get("display_name", ""))
    )
    if person_id is None:
        return
    if store.get_friend_row(person_id) is None:
        return
    if bool(env.data.get("accept")):
        store.set_status(person_id, "accepted")
        _grant_trust(person_id)
    else:
        store.remove_friend(person_id)
    broadcast_roster()


async def handle_device_cert(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    """Another of our machines minted us a certificate — adopt it.

    Adoption replaces which person this machine speaks for, so it needs consent.
    That consent is the **invite this machine itself minted**: the peer only got a
    session by redeeming a single-use pairing token, which the handshake records as
    `trusted`. An untrusted peer offering a certificate is refused, which is what
    stops a stranger talking this node out of its identity.

    Note the check is *not* "do I already hold a person key" — a machine generates
    one the moment anything asks who it is, so that test would refuse every real
    second computer.
    """
    cert = env.data.get("cert")
    if session is None or not session.info.trusted:
        logger.info("ignoring device cert from %s: peer is not trusted", env.src)
        return
    if not isinstance(cert, dict) or not person_identity.verify_device_cert(cert):
        return
    me = node_identity.load_identity()
    if str(cert.get("node_id")) != me.node_id:
        logger.info("ignoring device cert from %s: not addressed to this node", env.src)
        return
    person_identity.save_profile(device_cert=cert, person_id=str(cert["person_id"]))
    # Re-file this machine under its new owner so the roster stops showing it as a
    # separate person.
    store.upsert_device(
        node_id=me.node_id,
        person_id=str(cert["person_id"]),
        node_public_key=me.public_key,
        label=str(cert.get("label") or node_identity.node_name()),
        cert=cert,
    )
    broadcast_roster()


async def _send_response(person_id: str, accept: bool) -> None:
    payload = {**_hello_payload(), "accept": accept}
    for node_id in reachable_nodes(person_id):
        try:
            await peer_hub.send_to(node_id, SOCIAL_FRIEND_RESPONSE, payload)
            return
        except Exception:
            continue


def reachable_nodes(person_id: str) -> list[str]:
    """That person's devices which currently have a live session, connected first."""
    online = online_nodes()
    devices = [d["node_id"] for d in store.list_devices(person_id)]
    return [n for n in devices if n in online]


# ---- browser-driven operations ----------------------------------------------------


async def _dial(person_id: str, address: str | None) -> str | None:
    """Get a live session to one of `person_id`'s machines, dialing if needed.

    Tries, in order: a session we already have, the caller-supplied address, the
    last address each known device answered on, and finally the Atlas presence
    directory. The directory goes last on purpose — a LAN address we already know
    is both faster and more likely to work than a published one, and this ordering
    means the whole flow still works with Atlas unconfigured or down.
    """
    reachable = reachable_nodes(person_id)
    if reachable:
        return reachable[0]
    candidates = [address] if address else []
    candidates += [
        d["last_address"] for d in store.list_devices(person_id) if d["last_address"]
    ]
    candidates += await directory.lookup(person_id)
    for candidate in candidates:
        try:
            info = await peer_hub.connect(candidate, "direct")
            return info.node_id
        except Exception as exc:
            logger.info("social dial %s failed: %s", candidate, exc)
    return None


async def add_friend(
    code: str, address: str | None = None, note: str | None = None
) -> tuple[Friend | None, str | None]:
    """Send a friend request to whoever owns `code`. Returns (friend, error)."""
    if not code.strip():
        return None, "enter a friend code"
    try:
        person_id = resolve_person_id(code)
    except ValueError as exc:
        return None, str(exc)
    me = person_identity.load_person()
    if person_id == me.person_id:
        return None, "that is your own friend code"

    store.upsert_friend(person_id, status="pending_out", note=note)
    node_id = await _dial(person_id, address)
    if node_id is None:
        # The row is kept: they may simply be offline, and the request will be
        # retried the next time one of their machines connects.
        broadcast_roster()
        return None, (
            "could not reach anyone at that friend code — they may be offline, or "
            "not published to the directory. Add them with an address to be sure."
        )
    try:
        await peer_hub.send_to(node_id, SOCIAL_FRIEND_REQUEST, _hello_payload())
    except Exception as exc:
        return None, f"could not send the request: {exc}"
    broadcast_roster()
    row = store.get_friend_row(person_id)
    return (store.build_friend(row, online_nodes()) if row else None), None


async def respond(person_id: str, accept: bool) -> None:
    """Accept or decline a pending inbound request."""
    row = store.get_friend_row(person_id)
    if row is None:
        return
    if accept:
        store.set_status(person_id, "accepted")
        _grant_trust(person_id)
    else:
        store.remove_friend(person_id)
    await _send_response(person_id, accept)
    broadcast_roster()


async def remove(person_id: str) -> None:
    _revoke_trust(person_id)
    store.remove_friend(person_id)
    broadcast_roster()


async def block(person_id: str) -> None:
    _revoke_trust(person_id, blocked=True)
    store.upsert_friend(person_id, status="blocked")
    broadcast_roster()


async def link_device(
    invite: str, label: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Claim another machine as one of ours, using its peer-fabric invite.

    Runs on the machine holding the person key: it dials the other box, mints it a
    certificate, and records it as one of our devices. The other end adopts the
    certificate in `handle_device_cert`.
    """
    if person_identity.is_linked_device():
        return None, "only the machine holding your person key can link devices"
    try:
        address, token = trust.parse_invite(invite)
    except Exception:
        return None, "that does not look like an invite"
    try:
        info = await peer_hub.connect(address, "direct", token=token)
    except Exception as exc:
        return None, f"could not reach that machine: {exc}"

    me = person_identity.load_person()
    cert = me.issue_device_cert(info.node_id, info.public_key, label or info.node_name)
    try:
        await peer_hub.send_to(info.node_id, SOCIAL_DEVICE_CERT, {"cert": cert})
    except Exception as exc:
        return None, f"could not hand over the certificate: {exc}"

    store.upsert_device(
        node_id=info.node_id,
        person_id=me.person_id,
        node_public_key=info.public_key,
        label=label or info.node_name,
        cert=cert,
        address=address,
    )
    trust.save_known_peer(info.node_id, {"trusted": True, "via": "own-device"})
    broadcast_roster()
    return cert, None


# ---- startup ----------------------------------------------------------------------


def _on_peer_event(event: str, data: dict[str, Any]) -> None:
    """Greet peers as they connect, and keep presence in the panel live."""
    if event != "peer_update":
        return
    peer = data.get("peer") or {}
    node_id = str(peer.get("node_id") or "")
    if node_id and peer.get("status") == "connected":
        asyncio.ensure_future(say_hello(node_id))
    broadcast_roster()


def register(hub: PeerHub) -> None:
    """Wire the social layer onto the fabric. Called once at network startup."""
    store.init_social_db()
    hub.register_handler(SOCIAL_HELLO, handle_hello)
    hub.register_handler(SOCIAL_FRIEND_REQUEST, handle_friend_request)
    hub.register_handler(SOCIAL_FRIEND_RESPONSE, handle_friend_response)
    hub.register_handler(SOCIAL_DEVICE_CERT, handle_device_cert)
    hub.subscribe(_on_peer_event)
    from backend.modules.social.agent_tools import register_social_tools

    register_social_tools()
    # Record this machine as a device of whoever it acts for, so the panel can show
    # "your machines" with no special case. Unconditional: `self_cert()` generates a
    # person key if this is a first boot, and returns the adopted certificate if the
    # machine has been linked.
    node = node_identity.load_identity()
    store.upsert_device(
        node_id=node.node_id,
        person_id=person_identity.effective_person_id(),
        node_public_key=node.public_key,
        label=node_identity.node_name(),
        cert=person_identity.self_cert(),
    )
