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
import time
import logging
from typing import TYPE_CHECKING, Any

from backend.modules.network import identity as node_identity
from backend.modules.network import trust
from backend.modules.network.hub import peer_hub
from backend.modules.social import identity as person_identity
from backend.modules.social import directory, handles, store
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


#: Who was online last time we looked, so a change can be named rather than
#: re-sent. Presence itself is still derived (see `online_nodes`); this is only the
#: previous *answer*, held in memory and correctly empty after a restart.
_last_presence: dict[str, str] = {}


def broadcast_roster() -> None:
    """Push the roster, and name any presence that actually changed.

    The snapshot alone was all that ever went out, which meant there was no "came
    online" signal anywhere in the system — every subscriber received a complete
    roster on every peer event and had to diff it themselves, and none of them did.
    That is why nothing could notify you when a friend appeared, and why the agent
    had nothing to attach a watch to.

    The diff is computed here, once, rather than in each consumer: the browser, the
    notification rules and the agent's watches all want the same answer, and three
    independent diffs of the same snapshot is three chances to disagree about who
    just arrived.
    """
    snap = snapshot()
    _emit("roster", snap.model_dump())

    global _last_presence
    current = {f.person_id: f.presence for f in snap.friends}
    for person_id, presence in current.items():
        was = _last_presence.get(person_id)
        # A person we have never seen before is not "coming online" — on the first
        # roster after a restart that would announce every friend who happens to be
        # connected, which is noise, not news.
        if was is None or was == presence:
            continue
        friend = next((f for f in snap.friends if f.person_id == person_id), None)
        if friend is None or friend.is_self:
            continue
        _emit(
            "presence",
            {
                "person_id": person_id,
                "display_name": friend.display_name,
                "handle": friend.handle,
                "presence": presence,
                "online": presence == "online",
            },
        )
    _last_presence = current


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
        handle=directory._signed_in_handle(),
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
    """Mark every known device of `person_id` as a trusted peer — stored **and** live.

    Stored alone used to be enough, because a session only ever existed with a
    trusted peer. Now a friend request travels over a stranger's session, so the
    session the friendship was accepted on has to be upgraded in place, or the new
    friend stays a stranger until one of you reconnects.
    """
    for device in store.list_devices(person_id):
        trust.save_known_peer(
            device["node_id"], {"trusted": True, "via": "friend", "blocked": False}
        )
        peer_hub.set_trusted(device["node_id"], True)


def _revoke_trust(person_id: str, *, blocked: bool = False) -> None:
    for device in store.list_devices(person_id):
        trust.save_known_peer(device["node_id"], {"trusted": False, "blocked": blocked})
        peer_hub.set_trusted(device["node_id"], False)


def _on_accepted(person_id: str) -> None:
    """Everything that happens when a friendship becomes real, in one place.

    There are three ways to arrive here — we accepted, they accepted, or the two
    requests crossed and settled themselves — and they used to each carry their own
    copy of "grant trust". Adding a second consequence to three sites is how one of
    them gets missed, so both live here now.

    The ladder mirror is **detached**. Two of the three callers are peer-message
    handlers running on the hub's receive loop, and `mirror_accept` talks to the
    game server over HTTP with an 8-second timeout; awaiting it inline would stall
    the socket that friendship arrived on for as long as the game server is slow.
    It is also allowed to fail — see `ladder.mirror_accept` on why a missing ladder
    half is not a failed friendship.
    """
    _grant_trust(person_id)

    from backend.modules.social import ladder

    _detach(ladder.mirror_accept(person_id))


#: Strong references to in-flight detached work (see `_detach`).
_mirror_tasks: set[asyncio.Task[Any]] = set()


def _detach(coro: Any) -> None:
    """Run `coro` in the background, holding a reference until it finishes.

    Two things this exists for. The event loop keeps only a *weak* reference to a
    task, so a bare `ensure_future` can be collected mid-flight and silently never
    complete. And there is no loop at all in a synchronous test that calls
    `register()` directly — closing the coroutine there keeps the "never awaited"
    warning from firing on work that was correctly skipped.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = asyncio.ensure_future(coro)
    _mirror_tasks.add(task)
    task.add_done_callback(_mirror_tasks.discard)


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


def dialable_address(session: Any) -> str | None:
    """An address this session can be dialed back on later, or None.

    A relay address (`relay:<node_id>`) names the node, not a place, so it stays
    valid wherever the friend goes. A direct address is dialable only when *we*
    dialed it (a `ws://…/peer-ws` URL); an inbound socket reports the far end's
    ephemeral port, which nobody can dial.
    """
    info = getattr(session, "info", None)
    address = str(getattr(info, "address", "") or "")
    if address.startswith("relay:"):
        return address
    if address.startswith(("ws://", "wss://")) and address.endswith("/peer-ws"):
        return address
    return None


async def handle_hello(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    person_id = _accept_cert(
        env, env.data.get("cert"), str(env.data.get("display_name", ""))
    )
    if person_id is None:
        return
    # Remember how this machine was reached, so reconnecting does not depend on the
    # presence directory being up. Nothing recorded a friend's address before, and
    # the reconnect after a restart had only Atlas to go on.
    address = dialable_address(session)
    own = _is_own_machine(person_id, env.src)
    if address is not None and (own or store.get_friend_row(person_id) is not None):
        store.upsert_device(
            node_id=env.src,
            person_id=person_id,
            node_public_key=str(
                (env.data.get("cert") or {}).get("node_public_key", "")
            ),
            label=str((env.data.get("cert") or {}).get("label") or env.src),
            address=address,
        )
    # A device of an existing friend just came online — re-grant trust so a machine
    # added to their account after we friended them is reachable too.
    row = store.get_friend_row(person_id)
    if row is not None and row["status"] == "accepted":
        _grant_trust(person_id)
    if own:
        # Another machine signed in to our account. Its certificate is signed by
        # the account's key, which is exactly what makes it ours.
        trust.save_known_peer(
            env.src, {"trusted": True, "via": "own-device", "blocked": False}
        )
        peer_hub.set_trusted(env.src, True)
    broadcast_roster()


def _is_own_machine(person_id: str, node_id: str) -> bool:
    """Whether `node_id` is another machine of the account this one is enrolled in.

    Only an *enrolled* machine has owners' machines: a signed-out one speaks for a
    local key no other machine holds.
    """
    return (
        person_identity.is_linked_device()
        and person_id == person_identity.effective_person_id()
        and node_id != node_identity.load_identity().node_id
    )


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
        _on_accepted(person_id)
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
        _on_accepted(person_id)
    else:
        store.remove_friend(person_id)
    broadcast_roster()


async def _send_response(person_id: str, accept: bool) -> None:
    payload = {**_hello_payload(), "accept": accept}
    for node_id in reachable_nodes(person_id, include_strangers=True):
        try:
            await peer_hub.send_to(node_id, SOCIAL_FRIEND_RESPONSE, payload)
            return
        except Exception:
            continue


#: How often the reconnect loop looks for friends without a live session.
RECONNECT_CHECK_S = 60.0
#: Per-person backoff after a failed dial, doubling up to the cap. A friend who is
#: simply offline would otherwise be dialed — relay and all — every minute forever.
RECONNECT_BACKOFF_S = (60.0, 30 * 60.0)
#: Friends dialed per round, so a large roster cannot stall the loop.
RECONNECT_BATCH = 20

_reconnect_task: asyncio.Task[None] | None = None
#: person_id -> (next attempt, current backoff).
_reconnect_backoff: dict[str, tuple[float, float]] = {}


async def reconnect_round(now: float | None = None) -> list[str]:
    """Dial the friends that have no live session and are due. Returns who connected.

    Nothing used to reconnect friends at all: a session existed only while both
    nodes stayed up, and after any restart the roster showed everyone offline until
    someone pressed connect by hand. A **pending** request is retried the same way
    and resent on connection — `add_friend` has always promised that ("retried the
    next time one of their machines connects"), and nothing did it.
    """
    now = time.monotonic() if now is None else now
    connected: list[str] = []
    rows = [
        (r.person_id, r.status)
        for r in store.list_friends()
        if r.status in ("accepted", "pending_out") and not r.is_self
    ]
    if person_identity.is_linked_device():
        # Our own other machines, so signing in on two computers joins them up.
        rows.insert(0, (person_identity.effective_person_id(), "self"))
    due = [
        (person_id, status)
        for person_id, status in rows
        if not reachable_nodes(person_id, include_strangers=True)
        and _reconnect_backoff.get(person_id, (0.0, 0.0))[0] <= now
    ][:RECONNECT_BATCH]
    for person_id, status in due:
        try:
            node_id = await _dial(person_id, None)
        except Exception:  # noqa: BLE001 - one friend must not stop the round
            logger.debug("reconnect to %s failed", person_id, exc_info=True)
            node_id = None
        if node_id is None:
            _, backoff = _reconnect_backoff.get(person_id, (0.0, 0.0))
            backoff = min(
                max(backoff * 2, RECONNECT_BACKOFF_S[0]), RECONNECT_BACKOFF_S[1]
            )
            _reconnect_backoff[person_id] = (now + backoff, backoff)
            continue
        _reconnect_backoff.pop(person_id, None)
        connected.append(person_id)
        if status == "pending_out":
            try:
                await peer_hub.send_to(node_id, SOCIAL_FRIEND_REQUEST, _hello_payload())
            except Exception:  # noqa: BLE001
                logger.debug("resending friend request to %s failed", person_id)
    if connected:
        broadcast_roster()
    return connected


async def _reconnect_loop() -> None:
    while True:
        await asyncio.sleep(RECONNECT_CHECK_S)
        try:
            await reconnect_round()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must outlive a bad round
            logger.debug("friend reconnect round failed", exc_info=True)


def start_reconnect() -> None:
    global _reconnect_task
    if _reconnect_task is None or _reconnect_task.done():
        _reconnect_task = asyncio.create_task(_reconnect_loop())


async def stop_reconnect() -> None:
    global _reconnect_task
    task, _reconnect_task = _reconnect_task, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


def reachable_nodes(person_id: str, *, include_strangers: bool = False) -> list[str]:
    """That person's devices which currently have a live session, connected first.

    `include_strangers` is for the friend handshake only: a request and its answer
    travel over a session that is not trusted yet, and nothing else may.
    """
    online = online_nodes()
    if include_strangers:
        online |= set(peer_hub.strangers)
    devices = [d["node_id"] for d in store.list_devices(person_id)]
    return [n for n in devices if n in online]


async def resolve_target(who: str) -> tuple[str | None, str | None]:
    """Turn whatever the user typed into a `person_id`. Returns (person_id, error).

    Accepts, in order of how exact each one is:

    1. **`@username`** — resolved through the game server's directory. Async, and
       the only form that needs the network, which is why this function exists
       alongside `friendcode.resolve_person_id` rather than replacing it.
    2. a **friend code** (`HD-XXXX-…`) or a bare 16-character **person id** —
       self-certifying and offline. `resolve_person_id` tells them apart by
       **length**, and deliberately does not fall back to "treat it as a raw id"
       when the checksum fails: that fallback would dial a mistyped code as if it
       were real.

    A **display name is not accepted here** — resolving one needs the roster, and
    this runs before someone is on it. Callers that have a roster (the agent's
    `_resolve`) try names themselves, and only when unambiguous.
    """
    who = (who or "").strip()
    if not who:
        return None, "enter a username or friend code"
    if handles.is_handle(who):
        entry = await handles.resolve(who)
        if entry is None:
            return None, f"no such username: {who}"
        # Remember their machines, reachable through the relay wherever they are.
        # This is what makes a username enough on its own: without it, finding the
        # machine behind a person needed the Atlas presence directory, which an
        # ordinary install has no credentials for.
        for cert in handles.verified_devices(entry):
            store.upsert_device(
                node_id=str(cert["node_id"]),
                person_id=str(entry["person_id"]),
                node_public_key=str(cert["node_public_key"]),
                label=str(cert.get("label") or cert["node_id"]),
                cert=cert,
                address=f"relay:{cert['node_id']}",
            )
        # First sighting of this person — record the name the directory gave, so the
        # roster row is not just a 16-character id while the request is pending.
        return str(entry["person_id"]), None
    try:
        return resolve_person_id(who), None
    except ValueError as exc:
        return None, str(exc)


# ---- browser-driven operations ----------------------------------------------------


#: How long the direct candidates get, together, before the relay is tried. Direct
#: is an optimization — the same LAN, a tailnet — not the path: an address from
#: someone else's network usually just times out, and waiting the full handshake
#: timeout on each of five before trying the relay made adding a friend feel broken.
DIRECT_DIAL_BUDGET_S = 4.0


async def _dial(person_id: str, address: str | None) -> str | None:
    """Get a live session to one of `person_id`'s machines, dialing if needed.

    A session we already have wins. Otherwise every **direct** candidate — the
    address the caller typed, each device's last good address, the addresses in
    their presence record — is tried at once for `DIRECT_DIAL_BUDGET_S`, and the
    first handshake to finish wins. Only then the **relay** candidates, which reach
    a node wherever it is. Directory lookups stay best-effort: with Atlas down the
    known addresses still work.
    """
    reachable = reachable_nodes(person_id, include_strangers=True)
    if reachable:
        return reachable[0]
    candidates = [address] if address else []
    candidates += [
        d["last_address"] for d in store.list_devices(person_id) if d["last_address"]
    ]
    candidates += await directory.lookup(person_id)
    # Every machine we know is reachable through the relay by its id alone. Without
    # this, a friend made before the relay existed — no `relay:` address recorded —
    # was only ever redialed at their old LAN address, and never found again once
    # either side left that network.
    candidates += [f"relay:{d['node_id']}" for d in store.list_devices(person_id)]
    # Never dial ourselves: our own machine is on our own person's device list.
    me = node_identity.load_identity().node_id
    candidates = list(dict.fromkeys(c for c in candidates if c and c != f"relay:{me}"))
    direct = [c for c in candidates if not c.startswith("relay:")]
    relayed = [c for c in candidates if c.startswith("relay:")]

    node_id = await _first_direct(direct)
    if node_id is not None:
        return node_id
    if relayed and not any(t.name == "relay" for t in peer_hub.transports):
        logger.info(
            "social dial: %s is only reachable by relay, and it is off", person_id
        )
    for candidate in relayed:
        try:
            info = await peer_hub.connect(candidate, "relay")
            return info.node_id
        except Exception as exc:  # noqa: BLE001
            logger.info("social dial %s failed: %s", candidate, exc)
    return None


async def _first_direct(candidates: list[str]) -> str | None:
    """Race the direct candidates; the first completed handshake wins."""
    if not candidates:
        return None
    tasks = [
        asyncio.ensure_future(
            peer_hub.connect(c, "direct", timeout=DIRECT_DIAL_BUDGET_S)
        )
        for c in candidates
    ]
    try:
        for next_done in asyncio.as_completed(tasks, timeout=DIRECT_DIAL_BUDGET_S):
            try:
                info = await next_done
            except Exception as exc:  # noqa: BLE001
                logger.debug("social direct dial failed: %s", exc)
                continue
            return info.node_id
    except TimeoutError:
        pass
    finally:
        for task in tasks:
            task.cancel()
    return None


async def add_friend(
    code: str, address: str | None = None, note: str | None = None
) -> tuple[Friend | None, str | None]:
    """Send a friend request to whoever owns `code` — a `@username` or a friend
    code. Returns (friend, error)."""
    person_id, error = await resolve_target(code)
    if person_id is None:
        return None, error
    if person_id == person_identity.effective_person_id():
        return None, "that is your own friend code"

    store.upsert_friend(person_id, status="pending_out", note=note)
    node_id = await _dial(person_id, address)
    if node_id is None:
        # The row is kept: they may simply be offline, and the request will be
        # retried the next time one of their machines connects.
        broadcast_roster()
        # Say which of the two it is. The old message ended "add them with an
        # address to be sure", which read as if some usernames needed one; an
        # address almost never helps, since a username is dialed through the
        # relay by node id wherever the machine is.
        if not store.list_devices(person_id):
            return None, (
                "that account has no machine signed in yet — ask them to sign in "
                "on this app, then send the request again"
            )
        return None, (
            "none of their machines is online right now — the request is saved "
            "and goes through the next time one connects"
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
        _on_accepted(person_id)
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


# ---- startup ----------------------------------------------------------------------


def _on_peer_event(event: str, data: dict[str, Any]) -> None:
    """Greet peers as they connect, and keep presence in the panel live."""
    if event == "stranger_connected":
        # Greet strangers too: the hello carries our certificate, which is how their
        # node learns we are a friend's machine and upgrades the session.
        node_id = str(data.get("node_id") or "")
        if node_id:
            asyncio.ensure_future(say_hello(node_id))
        return
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
    # The friend handshake is the one conversation a stranger may have: each of these
    # verifies a device certificate before believing anything, and trust is granted
    # only when a person accepts.
    hub.allow_from_strangers(
        SOCIAL_HELLO, SOCIAL_FRIEND_REQUEST, SOCIAL_FRIEND_RESPONSE
    )
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
    # Link any roster rows that predate the ladder bridge (or whose person signed up
    # since we last looked). Detached and best-effort: `register` runs inside network
    # startup, and a slow or absent game server must not hold up the fabric — an
    # unreconciled roster renders fine, just without usernames.
    from backend.modules.social import ladder

    _detach(ladder.reconcile())
