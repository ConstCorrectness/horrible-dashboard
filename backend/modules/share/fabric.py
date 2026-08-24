"""Sessions across the peer fabric: invite, join, state, grants, actions, signaling.

The fabric already solves the hard parts — NAT traversal, relays, LAN discovery,
Ed25519 identity, trust — so this module only carries a small vocabulary and keeps
two mappings straight.

Wire types are declared **here rather than in `network/protocol.py`**, following
the training-ads and hassault precedent: a module owns its own vocabulary and
registers handlers for it, so adding a feature never edits the fabric core.

Trust is the fabric's, not ours, and it is only the *first* gate.
`session.info.trusted` says this envelope came from a friend — which buys
reachability and nothing else. What a friend may actually do inside a session is
decided by `gate.require`, every time. Knowing a session id is not membership,
and membership is not authority.

`share_signal` is deliberately a **pass-through**: it carries the SDP and ICE for
the browser-to-browser media link (Phase 3) and this node never inspects, stores
or re-encodes it. The fabric is the signaling channel; the pixels never come here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from backend.modules.share.models import RemoteSession, ShareInvite
from backend.modules.share.session import (
    CAPABILITY,
    CHANNEL,
    INVITE_TTL,
    share_manager,
)

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

# Peer-wire message types.
SHARE_INVITE = "share_invite"
SHARE_JOIN = "share_join"
SHARE_LEAVE = "share_leave"
SHARE_STATE = "share_state"
SHARE_END = "share_end"
SHARE_ACTION = "share_action"
SHARE_SIGNAL = "share_signal"
SHARE_MIRROR = "share_mirror"
SHARE_ERROR = "share_error"

#: Invites we could not deliver because the target's machine was not connected,
#: keyed by node id. `peer_hub.send_to` is a *direct* send with no relay in the
#: path, so an offline friend does not mean a queued invite somewhere — it means
#: nothing was sent and nobody ever finds out. This is the sender holding it,
#: which is the only place that can.
#:
#: Deliberately not persisted: it expires with `INVITE_TTL`, and a session does
#: not outlive the process hosting it.
_pending: dict[str, list[dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def _display_name(session: PeerSession, claimed_username: str) -> tuple[str, str]:
    """What to call whoever sent something: `(name, person_id)`.

    Precedence, most trustworthy first: the roster's own username for the person
    this node belongs to (the only one that is more than a claim), the sender's
    stamped username (a claim, but from an authenticated friend), then the device
    name, then a generic. The same ladder `hassault.fabric` applies, and it leaves
    the underlying rule untouched: the authenticated node id is the authority and
    everything here is a label.
    """
    from backend.modules.social import store

    person_id = ""
    try:
        person_id = store.person_for_node(session.info.node_id) or ""
        if person_id:
            row = store.get_friend_row(person_id) or {}
            if row.get("handle"):
                return f"@{row['handle']}", person_id
    except Exception:  # pragma: no cover - roster is best-effort here
        logger.debug("could not resolve a peer against the roster", exc_info=True)

    if claimed_username:
        return f"@{claimed_username}", person_id
    return (session.info.node_name or "a friend"), person_id


def _invite_payload(session_id: str, title: str) -> dict[str, Any]:
    """What goes on the wire, stamped with **our username** rather than only our
    machine name — the sender is the one place that knows its own username without
    asking anyone."""
    from backend.modules.games import server_auth
    from backend.modules.network import identity as node_identity

    return {
        "sessionId": session_id,
        "title": title,
        "fromUsername": server_auth.signed_in_username() or "",
        "fromDeviceName": node_identity.node_name(),
    }


# ---------------------------------------------------------------------------
# Host side
# ---------------------------------------------------------------------------


async def send_invite(node_id: str, session_id: str, title: str) -> None:
    """Invite one machine. Raises `KeyError` when that node is not connected."""
    from backend.modules.network.hub import peer_hub

    await peer_hub.send_to(node_id, SHARE_INVITE, _invite_payload(session_id, title))


def queue_invite(node_id: str, session_id: str, title: str) -> None:
    """Hold an invite for a machine that is not connected, to send when it is."""
    now = time.time()
    pending = [
        item for item in _pending.get(node_id, []) if now - item["ts"] <= INVITE_TTL
    ]
    # Keyed by session, so inviting the same person to the same session twice
    # while they are offline queues one invite, not two.
    pending = [item for item in pending if item["sessionId"] != session_id]
    pending.append({"sessionId": session_id, "title": title, "ts": now})
    _pending[node_id] = pending


async def flush_pending(node_id: str) -> None:
    """Send whatever we were holding for a node that just came online."""
    now = time.time()
    for item in _pending.pop(node_id, []):
        if now - item["ts"] > INVITE_TTL:
            continue
        try:
            await send_invite(node_id, item["sessionId"], item["title"])
        except KeyError:
            # Gone again between the event and this send. Put it back rather than
            # dropping it — it has not expired yet.
            queue_invite(node_id, item["sessionId"], item["title"])
        except Exception:
            logger.exception("could not flush a queued share invite to %s", node_id)


async def invite_person(person_id: str, session_id: str, title: str) -> int:
    """Invite every online machine of one person; queue for the rest.

    You invite a **human**, and they pick a box — the same rule hassault follows.
    Returns how many machines were reached right now.
    """
    from backend.modules.social import store

    sent = 0
    for device in store.list_devices(person_id):
        node_id = str(device["node_id"])
        try:
            await send_invite(node_id, session_id, title)
            sent += 1
        except KeyError:
            queue_invite(node_id, session_id, title)
        except Exception:
            logger.exception("could not invite %s to a share session", node_id)
    return sent


async def handle_join(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A friend's node asking to join the session we are hosting."""
    if not session.info.trusted:
        await _error(hub, session.info.node_id, env, "not a trusted peer")
        return
    hosting = share_manager.hosting
    data = env.data or {}
    if hosting is None or str(data.get("sessionId") or "") != hosting.id:
        # Knowing a session id is not membership, and a wrong id is not worth
        # confirming the shape of to anyone.
        await _error(hub, session.info.node_id, env, "no such session")
        return

    name, person_id = _display_name(session, str(data.get("username") or "")[:20])
    participant = await share_manager.add_participant(
        person_id=person_id or session.info.node_id,
        node_id=session.info.node_id,
        name=name,
    )
    if participant is None:
        await _error(hub, session.info.node_id, env, "session is not accepting joins")
        return
    # Answer with the whole session so the guest opens on what is actually there,
    # rather than an empty pane that fills in on the next mutation.
    await hub.send_to(
        session.info.node_id, SHARE_STATE, hosting.model_dump(), re=env.msg_id
    )
    # And the current projection, for the same reason — the host's layout may not
    # change again for minutes, so waiting for the next one means a guest who
    # joins into a blank map and has no way to tell that from a broken one.
    if share_manager.mirror is not None:
        try:
            await hub.send_to(
                session.info.node_id,
                SHARE_MIRROR,
                {"sessionId": hosting.id, "frame": share_manager.mirror},
            )
        except KeyError:
            pass


async def handle_leave(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    if not session.info.trusted:
        return
    await share_manager.remove_participant(session.info.node_id)


async def handle_action(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A guest asking to *do* something here.

    The one door. Both gates are applied in order and neither can be skipped:
    the **ladder** decides whether this participant is high enough, and the
    **host's own permission engine** then decides whether the underlying tool may
    run at all. A guest can therefore never exceed what the host's own agent
    rules allow — and, just as importantly, an action name this build has never
    heard of is refused rather than falling through.

    The `needs` field a guest sends is **advisory and ignored**. The rung comes
    from the registry, keyed by action name, because letting the caller nominate
    the permission it needs is letting the caller pick its own lock.
    """
    if not session.info.trusted:
        return
    from backend.modules.share import actions as action_registry
    from backend.modules.share.gate import require

    data = env.data or {}
    name = str(data.get("name") or "")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    participant = share_manager.participant_for_node(session.info.node_id)
    who = participant.name if participant else session.info.node_id

    spec = action_registry.spec(name)
    if spec is None:
        share_manager.audit.record(
            node_id=session.info.node_id,
            name=who,
            action=name or "(unnamed)",
            needs="?",
            outcome="denied",
            reason="unknown action",
        )
        await _publish_audit()
        await _error(hub, session.info.node_id, env, "unknown action")
        return

    ok, reason = require(participant, spec.needs)
    if not ok:
        share_manager.audit.record(
            node_id=session.info.node_id,
            name=who,
            action=name,
            needs=spec.needs,
            outcome="denied",
            reason=reason or "not allowed",
        )
        await _publish_audit()
        await _error(hub, session.info.node_id, env, reason or "not allowed")
        return

    if spec.extra_gate is not None:
        allowed, why_not = spec.extra_gate()
        if not allowed:
            share_manager.audit.record(
                node_id=session.info.node_id,
                name=who,
                action=name,
                needs=spec.needs,
                outcome="denied",
                reason=why_not,
            )
            await _publish_audit()
            await _error(hub, session.info.node_id, env, why_not)
            return

    decision, specifier = action_registry.permission_decision(name, params)
    if decision != "allow":
        # `ask` is recorded as `asked`, not `denied`: the host's rules said this
        # needs a human, and reporting it as a refusal would misrepresent both the
        # policy and what the guest should do next. Actuating on `ask` without
        # that human is the gap this whole file exists to prevent, so it stops
        # here until the approval path carries it.
        outcome = "denied" if decision == "deny" else "asked"
        why = (
            "the host's rules deny this"
            if decision == "deny"
            else "the host's rules require them to approve this"
        )
        share_manager.audit.record(
            node_id=session.info.node_id,
            name=who,
            action=name,
            needs=spec.needs,
            outcome=outcome,
            reason=why,
            detail={"specifier": specifier} if specifier else {},
        )
        await _publish_audit()
        await _error(hub, session.info.node_id, env, why)
        return

    if spec.audited:
        share_manager.audit.record(
            node_id=session.info.node_id,
            name=who,
            action=name,
            needs=spec.needs,
            outcome="allowed",
            detail={"specifier": specifier} if specifier else {},
        )
        await _publish_audit()

    # Actuation is the host's *browser*: panes, editors and terminals live there,
    # and this node has no more business driving them than it has decoding the
    # video. The action is relayed to the host's tabs, which is the same shape
    # the agent orchestrator uses for its own UI-driving tools.
    from backend.modules.ws import broadcast_event

    await broadcast_event(
        CHANNEL,
        "action",
        {
            "from": session.info.node_id,
            "name": name,
            "needs": spec.needs,
            "params": params,
            "ts": time.time(),
        },
    )


async def _publish_audit() -> None:
    """Push the audit log to the host's own tabs.

    Host-only, deliberately: it is broadcast on the local `/ws` channel and never
    sent to guests. A guest seeing the whole log would learn what every *other*
    guest did, which is the host's business and nobody else's.
    """
    from backend.modules.ws import broadcast_event

    await broadcast_event(
        CHANNEL,
        "audit",
        {"entries": [e.to_dict() for e in share_manager.audit.entries()]},
    )


async def handle_signal(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """Relay one SDP/ICE frame between two browsers.

    Pure pass-through. This node does not parse the payload, does not store it,
    and never sees a byte of the media it bootstraps — the whole reason the media
    path is browser-to-browser is that Python has no business in it.
    """
    if not session.info.trusted:
        return
    from backend.modules.ws import broadcast_event

    await broadcast_event(
        CHANNEL,
        "signal",
        {"from": session.info.node_id, "payload": (env.data or {}).get("payload")},
    )


async def send_signal(node_id: str, payload: Any) -> None:
    from backend.modules.network.hub import peer_hub

    try:
        await peer_hub.send_to(node_id, SHARE_SIGNAL, {"payload": payload})
    except KeyError:
        pass


async def send_action(
    session_id: str, name: str, params: dict[str, Any] | None = None
) -> None:
    """Ask the host of a joined session to do something, as a guest.

    Deliberately carries no `needs` rung. The host's registry decides what an
    action requires, keyed by its name — a guest nominating the permission its
    own action needs would be picking its own lock. Anything this node sent
    would be ignored there anyway, so not sending it keeps the wire honest about
    where the decision lives.
    """
    from backend.modules.network.hub import peer_hub

    remote = share_manager.joined.get(session_id)
    if remote is None:
        return
    try:
        await peer_hub.send_to(
            remote.host_node,
            SHARE_ACTION,
            {"sessionId": session_id, "name": name, "params": params or {}},
        )
    except KeyError:
        pass


# ---------------------------------------------------------------------------
# Guest side
# ---------------------------------------------------------------------------


async def handle_invite(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A friend asking us to join their session."""
    if not session.info.trusted:
        return
    data = env.data or {}
    session_id = str(data.get("sessionId") or "")
    if not session_id:
        return

    name, person_id = _display_name(session, str(data.get("fromUsername") or "")[:20])
    now = time.time()
    invite = ShareInvite(
        session_id=session_id,
        title=str(data.get("title") or "Shared session")[:80],
        host=session.info.node_id,
        host_name=name,
        host_device=str(data.get("fromDeviceName") or session.info.node_name or "")[
            :32
        ],
        person_id=person_id,
        ts=now,
        expires_at=now + INVITE_TTL,
    )
    await share_manager.record_invite(invite)
    await _notify_invite(invite)


async def _notify_invite(invite: ShareInvite) -> None:
    """Put the invite in front of the person, wherever they happen to be.

    The `share` broadcast above reaches the share pane — and only that pane, which
    unmounts on a workspace switch. `notify()` already fans out to the shell toast,
    the notification feed and the OS notification, with mute rules enforced at the
    producer; `dedupe` ties those surfaces to one invite so accepting on any of
    them clears the rest.
    """
    from backend.modules.notifications import service

    await service.notify(
        "invite",
        f"{invite.host_name} invited you to a shared session",
        invite.title + (f" · on {invite.host_device}" if invite.host_device else ""),
        person_id=invite.person_id or None,
        kind="info",
        data={
            "dedupe": f"share-invite:{invite.host}:{invite.session_id}",
            "action": "share.joinInvite",
            "invite": invite.model_dump(),
            "expires_at": invite.expires_at,
        },
    )


async def join_remote(session_id: str, host_node: str) -> tuple[bool, str | None]:
    """Ask a host to let us in, and adopt the session they hand back."""
    from backend.modules.games import server_auth
    from backend.modules.network.hub import peer_hub

    try:
        reply = await peer_hub.request(
            host_node,
            SHARE_JOIN,
            {
                "sessionId": session_id,
                "username": server_auth.signed_in_username() or "",
            },
        )
    except KeyError:
        return False, "that machine is not connected"
    except TimeoutError:
        return False, "the host did not answer"
    except Exception as exc:  # noqa: BLE001 - surfaced to the pane verbatim
        return False, str(exc)

    payload = reply.data or {}
    if reply.type == SHARE_ERROR:
        return False, str(payload.get("message") or "the host declined")

    me = peer_hub.identity().node_id
    mine = next(
        (
            p
            for p in payload.get("participants") or []
            if isinstance(p, dict) and p.get("node_id") == me
        ),
        None,
    )
    await share_manager.adopt_remote(
        RemoteSession(
            id=str(payload.get("id") or session_id),
            title=str(payload.get("title") or "Shared session"),
            host_node=host_node,
            host_name=str(payload.get("host_person") or host_node[:8]),
            grant=(mine or {}).get("grant") or "view",
            joined_at=time.time(),
        )
    )
    await share_manager.apply_remote_state(host_node, payload)
    return True, None


async def leave_remote(session_id: str) -> None:
    from backend.modules.network.hub import peer_hub

    remote = share_manager.joined.get(session_id)
    if remote is None:
        return
    try:
        await peer_hub.send_to(remote.host_node, SHARE_LEAVE, {"sessionId": session_id})
    except KeyError:
        pass
    await share_manager.drop_remote(session_id)


async def handle_mirror(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """A host published a projection of their workspace."""
    if not session.info.trusted:
        return
    await share_manager.apply_remote_mirror(session.info.node_id, dict(env.data or {}))


async def handle_state(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """The host published new session state."""
    if not session.info.trusted:
        return
    await share_manager.apply_remote_state(session.info.node_id, dict(env.data or {}))


async def handle_end(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    if not session.info.trusted:
        return
    session_id = str((env.data or {}).get("sessionId") or "")
    share_manager.drop_invite(session_id)
    await share_manager.drop_remote(session_id)


async def handle_error(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    if not session.info.trusted:
        return
    from backend.modules.ws import broadcast_event

    await broadcast_event(
        CHANNEL,
        "error",
        {
            "from": session.info.node_id,
            "message": str((env.data or {}).get("message") or "the host declined"),
        },
    )


async def _error(hub: PeerHub, node_id: str, env: PeerEnvelope, message: str) -> None:
    try:
        await hub.send_to(node_id, SHARE_ERROR, {"message": message}, re=env.msg_id)
    except KeyError:
        pass


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def _on_peer_event(event: str, data: dict[str, Any]) -> None:
    """Sweep a departed peer out of the session we host, and flush what we owe.

    A guest has no browser socket on this node, so nothing else would ever notice
    they left — without this they sit in the participant list forever, holding
    whatever rung they were given.
    """
    if event != "peer_update":
        return
    peer = data.get("peer") or {}
    node_id = str(peer.get("node_id") or "")
    if not node_id:
        return
    if peer.get("status") == "disconnected":
        asyncio.ensure_future(share_manager.remove_participant(node_id))
    elif _pending.get(node_id):
        asyncio.ensure_future(flush_pending(node_id))


def register(hub: PeerHub) -> None:
    """Register the fabric handlers and agent tools. Called from `network/setup.py`."""
    from backend.modules.share.agent_tools import register_share_tools

    register_share_tools()
    hub.register_handler(SHARE_INVITE, handle_invite)
    hub.register_handler(SHARE_JOIN, handle_join)
    hub.register_handler(SHARE_LEAVE, handle_leave)
    hub.register_handler(SHARE_STATE, handle_state)
    hub.register_handler(SHARE_END, handle_end)
    hub.register_handler(SHARE_ACTION, handle_action)
    hub.register_handler(SHARE_SIGNAL, handle_signal)
    hub.register_handler(SHARE_MIRROR, handle_mirror)
    hub.register_handler(SHARE_ERROR, handle_error)
    hub.subscribe(_on_peer_event)


__all__ = ["register", "CAPABILITY"]
