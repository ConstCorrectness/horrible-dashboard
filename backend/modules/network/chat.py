"""Peer chat: direct 1:1 messaging, **by person**.

A browser opens a conversation with someone over the `/ws` `peerchat` channel; the
backend routes it to whichever of that person's machines is online, relays it over
the signed peer wire (`peer_chat` envelope), mirrors it to this node's own browser
tabs, and writes it to `social_messages` in `app.db`.

**A conversation is with a human, not with a machine.** It used to be keyed by
`node_id` and held in an in-memory `deque`, which meant a friend with a laptop and
a desktop had two threads under two names, and a backend restart erased both. The
node id is still recorded on each message — as the route it took, not as its
identity. See `social/messages.py`.

An inbound message nobody has open raises a notification through
`notifications.service.notify`, which is where the mute rules are enforced; that is
what makes "mute any messages except for Andrew" mean anything.

This is the conversational counterpart to the `collab` channel (shared editable
state): `peerchat` is an append-only message log, `collab` is a synced document.
See docs/modules/social.mdx (Messages).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any

from backend.modules.network import protocol
from backend.modules.social import messages as message_store

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope
    from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

# Cap the fallback in-memory history (older messages drop off). Only messages that
# could not be filed under a person land here — see `_person_for`.
HISTORY_LIMIT = 200


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "peerchat", "event": event, "data": data}


def _person_for(node_id: str) -> str | None:
    """Whose machine this is, if we know. `None` for a node with no device row —
    a stranger, or a peer paired before the social layer existed."""
    from backend.modules.social import store as social_store

    try:
        return social_store.person_for_node(node_id)
    except Exception:
        logger.exception("could not resolve a person for node %s", node_id)
        return None


def _display_name(person_id: str, fallback: str) -> str:
    from backend.modules.social import store as social_store

    row = social_store.get_friend_row(person_id)
    return str(row["display_name"]) if row else fallback


class ChatManager:
    """Process-global registry of conversations and subscribed browser tabs."""

    def __init__(self) -> None:
        # Unfiled messages only: node_id -> recent messages, for peers we cannot
        # name a person for. Everything else is on disk, keyed by person.
        self._loose: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=HISTORY_LIMIT)
        )
        self._members: set[WsConnection] = set()
        # Which conversation each tab is looking at, so an arriving message can tell
        # "you are reading this" from "this needs a notification".
        self._viewing: dict[WsConnection, str] = {}

    # ---- browser side --------------------------------------------------------

    async def _fan_out(self, message: dict[str, Any]) -> None:
        payload = _evt("message", message)
        for conn in list(self._members):
            try:
                await conn.send_json(payload)
            except Exception:
                self._members.discard(conn)
                self._viewing.pop(conn, None)

    async def _broadcast_unread(self) -> None:
        payload = _evt("unread", {"counts": message_store.unread_counts()})
        for conn in list(self._members):
            try:
                await conn.send_json(payload)
            except Exception:
                self._members.discard(conn)
                self._viewing.pop(conn, None)

    async def handle(self, conn: WsConnection, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        data = msg.get("data") or {}
        if event == "open":
            self._members.add(conn)
            person_id = str(data.get("personId", ""))
            if not person_id:
                # A tab that only wants the badges (the People pane's list) opens
                # with no conversation.
                await self._broadcast_unread()
                return
            self._viewing[conn] = person_id
            message_store.mark_read(person_id)
            await conn.send_json(
                _evt(
                    "history",
                    {
                        "personId": person_id,
                        "messages": message_store.conversation(person_id),
                    },
                )
            )
            await self._broadcast_unread()
        elif event == "send":
            await self._send(conn, data)
        elif event == "read":
            person_id = str(data.get("personId", ""))
            if person_id and message_store.mark_read(person_id):
                await self._broadcast_unread()
        elif event == "unread":
            self._members.add(conn)
            await self._broadcast_unread()
        elif event == "close":
            self._members.discard(conn)
            self._viewing.pop(conn, None)

    # ---- sending -------------------------------------------------------------

    async def send_to_person(self, person_id: str, text: str) -> dict[str, Any]:
        """Send to whichever of someone's machines is online.

        Raises `KeyError` when none is — the caller reports that in its own idiom
        (an error event to the panel, an `{"error": …}` to the agent).
        """
        from backend.modules.social import roster

        nodes = roster.reachable_nodes(person_id)
        if not nodes:
            raise KeyError(person_id)
        return await self._deliver(person_id, nodes[0], text)

    async def send_to_peer(self, node_id: str, text: str) -> None:
        """Send to one specific machine.

        Kept for callers that genuinely mean a machine (device linking, tests). It
        files the message under that node's person when there is one, so an
        agent-sent message still lands in the human's conversation.
        """
        await self._deliver(_person_for(node_id), node_id, text)

    async def _deliver(
        self, person_id: str | None, node_id: str, text: str
    ) -> dict[str, Any]:
        from backend.modules.network.hub import peer_hub

        me = peer_hub.identity().node_name
        await peer_hub.send_to(
            node_id, protocol.PEER_CHAT, {"text": text, "from_name": me}
        )
        if person_id:
            message = message_store.record(
                person_id,
                direction="out",
                author=me,
                body=text,
                node_id=node_id,
            )
        else:
            message = {
                "id": uuid.uuid4().hex,
                "personId": None,
                "nodeId": node_id,
                "from": me,
                "text": text,
                "ts": time.time(),
                "direction": "out",
                "read": True,
            }
            self._loose[node_id].append(message)
        await self._fan_out(message)
        return message

    async def _send(self, conn: WsConnection, data: dict[str, Any]) -> None:
        person_id = str(data.get("personId", ""))
        text = str(data.get("text", "")).strip()
        if not person_id or not text:
            return
        try:
            await self.send_to_person(person_id, text)
        except KeyError:
            await conn.send_json(
                _evt(
                    "error",
                    {
                        "personId": person_id,
                        "message": "none of their machines is online",
                    },
                )
            )

    # ---- receiving -----------------------------------------------------------

    async def apply_peer_chat(self, env: PeerEnvelope) -> None:
        """A chat message arrived from a peer — file it, fan it out, and notify if
        nobody is looking at that conversation."""
        node_id = env.src
        text = str(env.data.get("text", ""))
        from_name = str(env.data.get("from_name", node_id))
        if not text:
            return
        person_id = _person_for(node_id)
        if person_id is None:
            message = {
                "id": env.msg_id,
                "personId": None,
                "nodeId": node_id,
                "from": from_name,
                "text": text,
                "ts": env.ts,
                "direction": "in",
                "read": True,
            }
            self._loose[node_id].append(message)
            await self._fan_out(message)
            return

        watching = person_id in self._viewing.values()
        name = _display_name(person_id, from_name)
        message = message_store.record(
            person_id,
            direction="in",
            author=name,
            body=text,
            node_id=node_id,
            ts=env.ts,
            message_id=env.msg_id,
            read=watching,
        )
        await self._fan_out(message)
        await self._broadcast_unread()
        if not watching:
            # Muted conversations stop here: the check is inside `notify`, at the
            # producer, so a silenced message never reaches the browser at all.
            from backend.modules.notifications.service import notify

            await notify(
                "message",
                name,
                text,
                person_id=person_id,
                data={"conversation": person_id},
            )

    def drop(self, conn: WsConnection) -> None:
        self._members.discard(conn)
        self._viewing.pop(conn, None)


chat_manager = ChatManager()


async def handle_chat_message(conn: WsConnection, msg: dict[str, Any]) -> None:
    await chat_manager.handle(conn, msg)


async def handle_peer_chat(
    hub: PeerHub, session: PeerSession, env: PeerEnvelope
) -> None:
    await chat_manager.apply_peer_chat(env)
