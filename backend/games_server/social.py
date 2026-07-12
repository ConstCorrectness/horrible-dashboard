"""The Plaza: the game server's **human** social layer — a Habbo-style lobby.

Where [AgentTown](town.py) is a fish tank of *agents* the human watches, the Plaza
is where the **real users** hang out: you appear as an avatar in a room, walk
around by clicking, talk in speech bubbles, see who else is online, add friends,
and challenge active players straight to a game. It's the answer to "signing in is
dull and there's no way to find someone to play."

Unlike the town, the Plaza is **event-driven, not ticked** — humans move
continuously, so a move/say broadcasts to the room immediately (Habbo feel), with
no world clock. Two broadcasts carry it all:

- ``social_state`` — one *room's* live state (occupants + the recent speech
  bubbles), sent to that room's occupants whenever it changes.
- ``social_roster`` — the *global* who's-online list (name, avatar, room, and
  current activity like "In a Tic-Tac-Toe match"), sent to everyone whenever
  someone joins, leaves, switches rooms, or changes activity. Movement inside a
  room does **not** rebroadcast the roster — only the room.

Identity is per-account, like the town: one presence per account, controlled by
the account's most recent connection. Friends and gamified profiles (XP/level)
persist in [store.py](store.py); the Plaza reads them to decorate the roster and
routes friend requests between online users.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any

from backend.games_server import models

logger = logging.getLogger(__name__)

# The rooms that declutter the main lobby. `plaza` is the default landing room;
# the others give matches, chat, and tournaments their own space. The catalog is
# advertised to clients so the room switcher stays data-driven.
ROOMS: tuple[dict[str, str], ...] = (
    {"id": "plaza", "name": "Central Plaza", "icon": "🏛"},
    {"id": "arcade", "name": "Arcade", "icon": "🕹"},
    {"id": "lounge", "name": "The Lounge", "icon": "🛋"},
    {"id": "arena", "name": "Tournament Arena", "icon": "⚔️"},
)
ROOM_IDS = tuple(r["id"] for r in ROOMS)
DEFAULT_ROOM = "plaza"

# The room floor is a 0..100 square in both axes; avatars walk within it.
FLOOR_MIN, FLOOR_MAX = 0.0, 100.0
SPAWN_X, SPAWN_Y = 50.0, 60.0

SAY_MAX_CHARS = 200
NAME_MAX_CHARS = 40
# Recent bubbles kept per room so a late joiner still sees the last few lines; the
# client fades each one out by its timestamp.
BUBBLE_LIMIT = 12


def _clamp(v: float) -> float:
    return max(FLOOR_MIN, min(FLOOR_MAX, v))


class Presence:
    """One online user in the Plaza. `session` is the live connection controlling
    it (the account's most recent); dropping the connection removes the presence."""

    def __init__(self, account_id: str, name: str, avatar: str, session: Any) -> None:
        self.account_id = account_id
        self.name = name
        self.avatar = avatar
        self.session = session
        self.room = DEFAULT_ROOM
        self.x = SPAWN_X + random.uniform(-8.0, 8.0)
        self.y = SPAWN_Y + random.uniform(-8.0, 8.0)
        # A short human-readable status shown in the roster: idle by default, or
        # e.g. "In a Tic-Tac-Toe match" while seated at a table.
        self.activity = "In the lobby"
        self.joined_at = time.time()

    def public(self) -> dict[str, Any]:
        """How this user renders on the room floor."""
        return {
            "account_id": self.account_id,
            "name": self.name,
            "avatar": self.avatar,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
        }

    def roster(self, level: int) -> dict[str, Any]:
        """How this user appears in the global who's-online list."""
        return {
            "account_id": self.account_id,
            "name": self.name,
            "avatar": self.avatar,
            "room": self.room,
            "activity": self.activity,
            "level": level,
        }


class SocialHub:
    """The human lobby: presences, per-room speech bubbles, and the roster."""

    def __init__(self) -> None:
        # account_id -> presence (one per account).
        self._presence: dict[str, Presence] = {}
        # room_id -> recent speech bubbles (fades client-side).
        self._bubbles: dict[str, list[dict[str, Any]]] = {r: [] for r in ROOM_IDS}

    # ---- dispatch (called by GameHub for social_/friend_/profile_ messages) ----

    async def handle(self, session: Any, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == models.SOCIAL_JOIN:
            await self._join(session, msg)
        elif mtype == models.SOCIAL_LEAVE:
            await self._leave(session)
        elif mtype == models.SOCIAL_MOVE:
            await self._move(session, msg)
        elif mtype == models.SOCIAL_ROOM:
            await self._switch_room(session, msg)
        elif mtype in (models.SOCIAL_SAY, models.SOCIAL_EMOTE):
            await self._say(session, msg, emote=mtype == models.SOCIAL_EMOTE)
        elif mtype == models.FRIEND_REQUEST:
            await self._friend_request(session, msg)
        elif mtype == models.FRIEND_ACCEPT:
            await self._friend_accept(session, msg)
        elif mtype == models.FRIEND_REMOVE:
            await self._friend_remove(session, msg)
        elif mtype == models.FRIEND_LIST:
            await self._send_friends(session)
        elif mtype == models.PROFILE_GET:
            await self._send_profile(session)
        elif mtype == models.PROFILE_SET:
            await self._set_profile(session, msg)

    # ---- presence lifecycle -----------------------------------------------------

    async def _join(self, session: Any, msg: dict[str, Any]) -> None:
        from backend.games_server import store

        account_id = session.account_id
        name = str(msg.get("name") or session.display_name or account_id)[
            :NAME_MAX_CHARS
        ]
        avatar = str(msg.get("avatar") or "🙂")[:8]
        # Remember avatar on the persistent profile so it survives reconnects.
        store.upsert_profile(account_id, avatar=avatar)

        presence = self._presence.get(account_id)
        if presence is None:
            presence = Presence(account_id, name, avatar, session)
            self._presence[account_id] = presence
        else:
            # Rejoin: the newest connection takes control.
            presence.session = session
            presence.name, presence.avatar = name, avatar
        await self._send(
            session,
            {
                "type": models.SOCIAL_JOINED,
                "you": presence.public(),
                "rooms": list(ROOMS),
                **self._room_snapshot(presence.room),
            },
        )
        await self._send_profile(session)
        await self._send_friends(session)
        await self._broadcast_room(presence.room)
        await self._broadcast_roster()

    async def _leave(self, session: Any) -> None:
        presence = self._presence.get(session.account_id)
        if presence is not None and presence.session is session:
            room = presence.room
            del self._presence[session.account_id]
            await self._broadcast_room(room)
            await self._broadcast_roster()

    def on_disconnect(self, session: Any) -> None:
        """The user's node went away: drop their presence from the lobby. (Unlike
        the town's sleep-in-place fish, a human simply leaves.) Fire-and-forget so
        the hub's disconnect path stays synchronous."""
        import asyncio

        presence = self._presence.get(session.account_id)
        if presence is not None and presence.session is session:
            room = presence.room
            del self._presence[session.account_id]
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self._broadcast_room(room))
            loop.create_task(self._broadcast_roster())

    async def _move(self, session: Any, msg: dict[str, Any]) -> None:
        presence = self._presence.get(session.account_id)
        if presence is None:
            return
        presence.x = _clamp(float(msg.get("x", presence.x)))
        presence.y = _clamp(float(msg.get("y", presence.y)))
        # Movement rebroadcasts only the room (roster is unaffected — keeps the
        # global list quiet while people wander).
        await self._broadcast_room(presence.room)

    async def _switch_room(self, session: Any, msg: dict[str, Any]) -> None:
        presence = self._presence.get(session.account_id)
        if presence is None:
            return
        room = str(msg.get("room") or "")
        if room not in ROOM_IDS or room == presence.room:
            return
        old = presence.room
        presence.room = room
        presence.x = SPAWN_X + random.uniform(-8.0, 8.0)
        presence.y = SPAWN_Y + random.uniform(-8.0, 8.0)
        await self._send(
            session,
            {
                "type": models.SOCIAL_JOINED,
                "you": presence.public(),
                "rooms": list(ROOMS),
                **self._room_snapshot(room),
            },
        )
        await self._broadcast_room(old)
        await self._broadcast_room(room)
        await self._broadcast_roster()

    async def _say(self, session: Any, msg: dict[str, Any], *, emote: bool) -> None:
        presence = self._presence.get(session.account_id)
        if presence is None:
            return
        text = str(msg.get("text") or "")[:SAY_MAX_CHARS]
        if not text:
            return
        bubble = {
            "id": uuid.uuid4().hex[:8],
            "account_id": presence.account_id,
            "name": presence.name,
            "avatar": presence.avatar,
            "text": text,
            "emote": emote,
            "x": round(presence.x, 1),
            "y": round(presence.y, 1),
            "ts": time.time(),
        }
        bubbles = self._bubbles.setdefault(presence.room, [])
        bubbles.append(bubble)
        if len(bubbles) > BUBBLE_LIMIT:
            del bubbles[: len(bubbles) - BUBBLE_LIMIT]
        await self._broadcast_room(presence.room)

    # ---- friends (persisted; routed between online users) -----------------------

    async def _friend_request(self, session: Any, msg: dict[str, Any]) -> None:
        from backend.games_server import store

        target = str(msg.get("account_id") or "")
        if not target or target == session.account_id:
            return
        store.request_friend(session.account_id, target)
        await self._send_friends(session)
        await self._notify_friends(target)  # push the pending badge if they're online

    async def _friend_accept(self, session: Any, msg: dict[str, Any]) -> None:
        from backend.games_server import store

        other = str(msg.get("account_id") or "")
        if not other:
            return
        store.accept_friend(session.account_id, other)
        await self._send_friends(session)
        await self._notify_friends(other)

    async def _friend_remove(self, session: Any, msg: dict[str, Any]) -> None:
        from backend.games_server import store

        other = str(msg.get("account_id") or "")
        if not other:
            return
        store.remove_friend(session.account_id, other)
        await self._send_friends(session)
        await self._notify_friends(other)

    async def _notify_friends(self, account_id: str) -> None:
        """Push a fresh friends list to `account_id` if they're online."""
        presence = self._presence.get(account_id)
        if presence is not None:
            await self._send_friends(presence.session)

    async def _send_friends(self, session: Any) -> None:
        from backend.games_server import store

        account_id = session.account_id
        friends = store.list_friends(account_id)
        online = set(self._presence)
        for f in friends:
            f["online"] = f["account_id"] in online
        await self._send(
            session,
            {
                "type": models.FRIENDS,
                "friends": friends,
                "pending": store.list_pending(account_id),
            },
        )

    # ---- gamified profile -------------------------------------------------------

    async def _send_profile(self, session: Any) -> None:
        from backend.games_server import store

        await self._send(
            session, {"type": models.PROFILE, **store.get_profile(session.account_id)}
        )

    async def _set_profile(self, session: Any, msg: dict[str, Any]) -> None:
        from backend.games_server import store

        store.upsert_profile(
            session.account_id,
            avatar=str(msg["avatar"])[:8] if msg.get("avatar") else None,
            bio=str(msg["bio"])[:280] if msg.get("bio") is not None else None,
        )
        presence = self._presence.get(session.account_id)
        if presence is not None and msg.get("avatar"):
            presence.avatar = str(msg["avatar"])[:8]
            await self._broadcast_room(presence.room)
            await self._broadcast_roster()
        await self._send_profile(session)

    # ---- roster activity (called by the hub when a user sits at a table) --------

    async def set_activity(self, account_id: str, activity: str) -> None:
        presence = self._presence.get(account_id)
        if presence is not None and presence.activity != activity:
            presence.activity = activity
            await self._broadcast_roster()

    # ---- views + broadcast ------------------------------------------------------

    def _room_snapshot(self, room: str) -> dict[str, Any]:
        return {
            "room": room,
            "occupants": [
                p.public() for p in self._presence.values() if p.room == room
            ],
            "bubbles": list(self._bubbles.get(room, [])),
        }

    async def _broadcast_room(self, room: str) -> None:
        state = {"type": models.SOCIAL_STATE, **self._room_snapshot(room)}
        for presence in list(self._presence.values()):
            if presence.room == room:
                await self._send(presence.session, state)

    async def _broadcast_roster(self) -> None:
        from backend.games_server import store

        online = [
            p.roster(store.get_profile(p.account_id)["level"])
            for p in self._presence.values()
        ]
        online.sort(key=lambda r: (-r["level"], r["name"].lower()))
        msg = {"type": models.SOCIAL_ROSTER, "online": online}
        for presence in list(self._presence.values()):
            await self._send(presence.session, msg)

    async def _send(self, session: Any, msg: dict[str, Any]) -> None:
        try:
            await session.conn.send_json(msg)
        except Exception:
            logger.debug("plaza: failed to send to a session", exc_info=True)
