"""The share session: process-global on the host node.

The shape is karaoke's and hassault's, for the same reason: **the server owns
intent and every client is a renderer**. One session lives in this process, every
mutation bumps a revision and rebroadcasts the whole state, and so a second tab,
a phone on the LAN, or the agent with no pane open all see the same thing without
any of them owning it.

A node hosts at most one session at a time. That is a deliberate limit rather
than a stub: a session mirrors *this node's workspace*, and there is only one of
those, so a second concurrent session would be a second view of the same thing
with a different guest list — which is a grant list, not a session.

Guest side is separate and plural: this node may have joined several sessions
hosted elsewhere.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from backend.modules.share import link as link_api
from backend.modules.share.audit import AuditLog
from backend.modules.share.models import (
    GrantLevel,
    Invitee,
    Participant,
    RemoteSession,
    SessionOut,
    ShareInvite,
    ShareMode,
    ShareSession,
)

logger = logging.getLogger(__name__)

CHANNEL = "share"

#: The capability a node advertises when it can host or join a session. Used so a
#: friend's UI offers only people who could actually accept.
CAPABILITY = "share"

#: How long an invitation stays live. Matches hassault's, and for the same
#: reason: the thing being invited to is state in this process, so an invite that
#: outlived it would point at nothing.
INVITE_TTL = 300.0


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": CHANNEL, "event": event, "data": data}


class ShareManager:
    """Process-global registry: what we host, what we have joined, what we owe."""

    def __init__(self) -> None:
        self.hosting: ShareSession | None = None
        self.joined: dict[str, RemoteSession] = {}
        self._invites: dict[str, ShareInvite] = {}
        #: The latest redacted projection of the host's workspace, exactly as the
        #: host's browser produced it. Held opaquely on purpose: the redaction
        #: happened in the only place that could do it (the host's own browser,
        #: which is where pane declarations live), and a backend that reached into
        #: this to "help" would be a second authority on a decision that already
        #: has one. Stored so a guest who joins mid-session gets the whole picture
        #: instead of waiting for the host to move something.
        self.mirror: dict[str, Any] | None = None
        #: Projections received from hosts of sessions we have joined, by session id.
        self.remote_mirrors: dict[str, dict[str, Any]] = {}
        #: The public link's token and ingest URL, held **here and not on
        #: `ShareSession`**. The session model is broadcast verbatim to every
        #: guest, and the ingest URL is publish authority: a guest holding it
        #: could push their own video over the host's stream. Only the view URL
        #: — which is public by construction — goes in the model; the host's own
        #: browser fetches the ingest URL from `GET /api/share/link`, a route no
        #: guest can reach.
        self.link_token: str = ""
        self.link_ingest: str = ""
        self.link_expires_at: float = 0.0
        #: What each guest has actually done. Bounded and in-memory — a session
        #: is a live thing, not a compliance record. Host-only: it is served and
        #: broadcast locally and never sent to a guest, because one guest reading
        #: it would learn what every other guest did.
        self.audit = AuditLog()

    # ---- read -------------------------------------------------------------

    def live_invites(self) -> list[ShareInvite]:
        """Invitations that have not aged out. Pruned on read rather than on a
        timer — nothing else needs waking up to keep a short list tidy."""
        now = time.time()
        for session_id, invite in list(self._invites.items()):
            if invite.expires_at <= now:
                self._invites.pop(session_id, None)
        return sorted(self._invites.values(), key=lambda i: i.ts, reverse=True)

    def snapshot(self) -> SessionOut:
        return SessionOut(
            hosting=self.hosting,
            joined=sorted(self.joined.values(), key=lambda s: s.joined_at),
            invites=self.live_invites(),
        )

    def participant_for_node(self, node_id: str) -> Participant | None:
        """The participant a peer envelope belongs to, or None.

        Resolved by **node**, because that is what the fabric authenticated. A
        person with two machines in one session is two rows, which is correct:
        a revoke has to reach a machine, not a concept.
        """
        if self.hosting is None:
            return None
        return next(
            (p for p in self.hosting.participants if p.node_id == node_id), None
        )

    # ---- host side --------------------------------------------------------

    async def start(self, title: str, mode: ShareMode) -> ShareSession:
        """Open a session. Starting while one is already live retitles it rather
        than orphaning the guests already inside."""
        from backend.modules.network.hub import peer_hub
        from backend.modules.social import roster

        me = peer_hub.identity()
        if self.hosting is not None:
            self.hosting.title = title or self.hosting.title
            self.hosting.mode = mode
            await self._publish()
            return self.hosting

        profile = roster.self_profile()
        now = time.time()
        self.hosting = ShareSession(
            id=uuid.uuid4().hex[:12],
            title=title or "Shared session",
            mode=mode,
            host_node=me.node_id,
            host_person=profile.person_id,
            created_at=now,
            participants=[
                Participant(
                    person_id=profile.person_id,
                    node_id=me.node_id,
                    name=profile.display_name,
                    role="host",
                    grant="control",
                    joined_at=now,
                )
            ],
        )
        await self._publish()
        logger.info("share session %s started", self.hosting.id)
        return self.hosting

    async def stop(self) -> None:
        """Close the session and tell every guest, so nobody is left rendering a
        session that no longer exists."""
        session = self.hosting
        if session is None:
            return
        # Revoke before forgetting the token, or the link outlives the session it
        # was minted for — the exact failure the whole expiry story exists to
        # bound. Best effort: an unreachable relay must not block a stop.
        await self.revoke_link()
        self.hosting = None
        self.mirror = None
        # The log belongs to the session. Carrying one session's actions into the
        # next would attribute them to whoever is in the room then.
        self.audit.clear()
        await self._tell_guests(session, "share_end", {"sessionId": session.id})
        await _broadcast(_evt("ended", {"sessionId": session.id}))
        logger.info("share session %s stopped", session.id)

    async def mint_link(self, *, ttl_s: int | None = None, passphrase: str = "") -> str:
        """Mint a public link for the live session and publish it to every client.

        Replaces any existing link, revoking the old one first: two live links for
        one session means a revoke that only half works.
        """
        if self.hosting is None:
            raise link_api.LinkError("Start a session before minting a public link.")
        await self.revoke_link()
        handle = await link_api.mint(
            title=self.hosting.title, ttl_s=ttl_s, passphrase=passphrase
        )
        self.link_token = handle.token
        self.link_ingest = handle.ingest_url
        self.link_expires_at = handle.expires_at
        self.hosting.link = handle.view_url
        await self._publish()
        logger.info("share session %s minted a public link", self.hosting.id)
        return handle.view_url

    async def revoke_link(self) -> bool:
        """Kill the public link. Safe when there is none."""
        token = self.link_token
        self.link_token = ""
        self.link_ingest = ""
        self.link_expires_at = 0.0
        revoked = await link_api.revoke(token) if token else False
        if self.hosting is not None and self.hosting.link:
            self.hosting.link = ""
            await self._publish()
        return revoked

    async def add_participant(
        self, *, person_id: str, node_id: str, name: str
    ) -> Participant | None:
        """Admit a guest at the lowest rung. Rejoining from the same machine
        refreshes the row rather than adding a second one."""
        if self.hosting is None:
            return None
        existing = self.participant_for_node(node_id)
        if existing is not None:
            existing.name = name or existing.name
            await self._publish()
            return existing
        participant = Participant(
            person_id=person_id,
            node_id=node_id,
            name=name or node_id[:8],
            role="guest",
            grant="view",
            joined_at=time.time(),
        )
        self.hosting.participants.append(participant)
        await self._publish()
        return participant

    async def remove_participant(self, node_id: str) -> None:
        if self.hosting is None:
            return
        before = len(self.hosting.participants)
        self.hosting.participants = [
            p for p in self.hosting.participants if p.node_id != node_id
        ]
        if len(self.hosting.participants) != before:
            await self._publish()

    async def set_grant(self, person_id: str, grant: GrantLevel) -> bool:
        """Move every machine of one person to a rung.

        Addressed to a person for the same reason an invite is: you are deciding
        about a human, and asking which of their laptops may use the terminal is
        a question with no good answer.
        """
        if self.hosting is None:
            return False
        touched = False
        for participant in self.hosting.participants:
            if participant.person_id == person_id and participant.role != "host":
                participant.grant = grant
                touched = True
        if touched:
            await self._publish()
        return touched

    async def revoke_all(self) -> None:
        """Drop everyone to `view` in one move.

        The panic button. It deliberately does not end the session — nobody is
        dumped out mid-sentence — but nothing anyone holds can still act.
        """
        if self.hosting is None:
            return
        for participant in self.hosting.participants:
            if participant.role != "host":
                participant.grant = "view"
        await self._publish()

    async def _publish(self) -> None:
        """Bump the revision and push the whole session to local tabs and guests."""
        if self.hosting is None:
            return
        self.hosting.revision += 1
        payload = self.hosting.model_dump()
        await _broadcast(_evt("session", payload))
        await self._tell_guests(self.hosting, "share_state", payload)

    async def _tell_guests(
        self, session: ShareSession, msg_type: str, data: dict[str, Any]
    ) -> None:
        from backend.modules.network.hub import peer_hub

        for participant in session.participants:
            if participant.role == "host":
                continue
            try:
                await peer_hub.send_to(participant.node_id, msg_type, data)
            except KeyError:
                # Their machine went away. The peer-disconnect sweep in
                # `fabric._on_peer_event` removes the row; nothing to do here.
                pass
            except Exception:
                logger.debug(
                    "could not reach guest %s", participant.node_id, exc_info=True
                )

    async def set_mirror(
        self, payload: dict[str, Any], summary: dict[str, Any] | None = None
    ) -> None:
        """The host's browser published a new projection of its workspace.

        Dropped when no session is running rather than stored for later: a
        projection is only meaningful alongside the session it belongs to, and
        keeping one after `stop()` is how a workspace gets handed to the guests of
        the *next* session before its host has published anything.
        """
        if self.hosting is None:
            return
        self.mirror = payload
        # Counted by the sender, not derived here. The backend holds the
        # projection opaquely on purpose, and walking its tree to count panes
        # would be the first crack in that — one reader today, a reader that
        # "helpfully" filters something tomorrow.
        if summary is not None:
            panes = summary.get("panes")
            hidden = summary.get("hidden")
            self.hosting.mirror_panes = panes if isinstance(panes, int) else None
            self.hosting.mirror_hidden = hidden if isinstance(hidden, int) else None
            await _broadcast(_evt("session", self.hosting.model_dump()))
        await self._tell_guests(
            self.hosting,
            "share_mirror",
            {"sessionId": self.hosting.id, "frame": payload},
        )

    async def apply_remote_mirror(self, host_node: str, data: dict[str, Any]) -> None:
        """A host published a projection of a session we have joined."""
        session_id = str(data.get("sessionId") or "")
        remote = self.joined.get(session_id)
        # Checked against the session we joined, not merely against trust: a
        # trusted friend is still not the host of somebody else's session, and
        # accepting this on `src` alone would let any friend paint the pane.
        if remote is None or remote.host_node != host_node:
            return
        frame = data.get("frame")
        if not isinstance(frame, dict):
            return
        self.remote_mirrors[session_id] = frame
        await _broadcast(
            _evt("remote_mirror", {"sessionId": session_id, "frame": frame})
        )

    # ---- guest side -------------------------------------------------------

    async def record_invite(self, invite: ShareInvite) -> None:
        self._invites[invite.session_id] = invite
        await _broadcast(_evt("invite", invite.model_dump()))

    def drop_invite(self, session_id: str) -> None:
        self._invites.pop(session_id, None)

    async def adopt_remote(self, session: RemoteSession) -> None:
        self.joined[session.id] = session
        self.drop_invite(session.id)
        await _broadcast(_evt("joined", session.model_dump()))

    async def drop_remote(self, session_id: str) -> None:
        session = self.joined.pop(session_id, None)
        self.remote_mirrors.pop(session_id, None)
        if session is not None:
            await _broadcast(_evt("left", {"sessionId": session_id}))

    async def apply_remote_state(self, host_node: str, payload: dict[str, Any]) -> None:
        """The host published new session state.

        Update our row and relay it to this node's tabs, so a guest's pane shows
        the same participant list the host sees — including their own rung, which
        is the one thing a guest cannot work out for themselves.
        """
        session_id = str(payload.get("id") or "")
        remote = self.joined.get(session_id)
        if remote is None or remote.host_node != host_node:
            return
        me = _self_node_id()
        mine = next(
            (
                p
                for p in payload.get("participants") or []
                if isinstance(p, dict) and p.get("node_id") == me
            ),
            None,
        )
        if mine is not None:
            remote.grant = mine.get("grant") or remote.grant
        remote.title = str(payload.get("title") or remote.title)
        # `yourGrant` is resolved here rather than in the browser: a tab does not
        # know this node's id, so it cannot pick its own row out of the host's
        # participant list — and its own rung is the one thing a guest cannot
        # work out for itself.
        await _broadcast(
            _evt(
                "remote_session",
                {**payload, "hostNode": host_node, "yourGrant": remote.grant},
            )
        )


def _self_node_id() -> str:
    from backend.modules.network.hub import peer_hub

    return peer_hub.identity().node_id


async def _broadcast(message: dict[str, Any]) -> None:
    from backend.modules.ws import broadcast_event

    await broadcast_event(message["channel"], message["event"], message["data"])


async def list_invitees() -> list[Invitee]:
    """Friends who could join a session right now.

    The roster lives in the social module and the pane must not import across
    that boundary, so the join happens here — the same shape as
    `GET /api/hassault/invitees`.
    """
    from backend.modules.network.hub import peer_hub
    from backend.modules.social import roster, store

    online = roster.online_nodes()
    capable = {
        p.node_id for p in peer_hub.list_peers() if CAPABILITY in (p.capabilities or [])
    }
    out: list[Invitee] = []
    for friend in store.list_friends(online):
        if friend.status != "accepted" or friend.is_self:
            continue
        nodes = [d.node_id for d in friend.devices if d.online]
        if not nodes:
            continue
        out.append(
            Invitee(
                name=friend.display_name,
                username=friend.handle or "",
                person_id=friend.person_id,
                friend_code=friend.friend_code,
                can_share=any(n in capable for n in nodes),
                devices_online=len(nodes),
            )
        )
    return out


share_manager = ShareManager()
