"""Lobbies: gather first, then look for a match together.

A search (`HassaultReferee.find_match`) seats one player. A group of friends
who each search get whatever room has space for each of them, which is the same
room only by luck. A lobby is the group, made before the search: one player
opens it and gets a short code, the others join with the code, and when the
leader presses play the server finds **one** room with a seat for every member
and tells them all.

It is HTTP and polled, on purpose. A lobby exists before anyone has a match
socket — the socket is opened by the join, after the map has loaded — and a
second socket only to wait in a menu would be a second thing to reconnect. The
state is a few hundred bytes and changes a handful of times; each member asks
for it every second or two, and asking is also how the server knows they are
still there.

Nothing here is an account. A member is a callsign and a random token the
server hands back on entry; the token is what says "this request is that
member" on every later call. Guests play here, and a lobby is no more trusted
than the rooms they could each have joined alone.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

#: Unambiguous when read aloud or typed from a screenshot: no 0/O, 1/I/L.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 5

#: A team, not a server's worth. The room still has to have this many seats.
MAX_MEMBERS = 5

#: A member who has not asked for the lobby in this long has closed the game.
MEMBER_SILENCE_S = 12.0

#: A lobby nobody is in is forgotten at once; one with people in it who have
#: all gone quiet goes with its last member.
MAX_LOBBIES = 200

NAME_LENGTH = 16


class LobbyError(ValueError):
    """A request the lobby refuses, with the reason a player would be told."""


@dataclass
class Member:
    token: str
    name: str
    seen: float


@dataclass
class Lobby:
    code: str
    members: list[Member] = field(default_factory=list)
    #: The room the last search found, and which search it was. A member acts
    #: on a `serial` once, so a lobby that plays twice is told twice.
    match: dict[str, Any] | None = None
    serial: int = 0
    #: What the leader last had selected, so the others see it before play.
    maps: list[str] = field(default_factory=list)
    mode: str = ""

    @property
    def leader(self) -> Member | None:
        """The longest-standing member. Nobody is elected: when the leader
        leaves, the next one simply is, as a room's host is."""
        return self.members[0] if self.members else None

    def member(self, token: str) -> Member | None:
        return next((m for m in self.members if m.token == token), None)

    def payload(self, token: str | None = None) -> dict[str, Any]:
        leader = self.leader
        return {
            "code": self.code,
            "members": [
                {"name": m.name, "leader": m is leader, "you": m.token == token}
                for m in self.members
            ],
            "leader": bool(leader and leader.token == token),
            "maxMembers": MAX_MEMBERS,
            "maps": list(self.maps),
            "mode": self.mode,
            "match": self.match,
            "serial": self.serial,
        }


def _clean_name(name: Any) -> str:
    text = str(name or "").strip()[:NAME_LENGTH]
    return text or "Operative"


class LobbyBook:
    """Every open lobby on this server."""

    def __init__(self) -> None:
        self._lobbies: dict[str, Lobby] = {}

    # -- upkeep -------------------------------------------------------------

    def _sweep(self, now: float) -> None:
        """Drop members who went quiet, and lobbies with nobody left."""
        for code in list(self._lobbies):
            lobby = self._lobbies[code]
            lobby.members = [
                m for m in lobby.members if now - m.seen <= MEMBER_SILENCE_S
            ]
            if not lobby.members:
                del self._lobbies[code]

    def _find(self, code: str) -> Lobby:
        lobby = self._lobbies.get(str(code or "").strip().upper())
        if lobby is None:
            raise LobbyError("no lobby has that code; it may have closed")
        return lobby

    def _seated(self, code: str, token: str, now: float) -> tuple[Lobby, Member]:
        lobby = self._find(code)
        member = lobby.member(token)
        if member is None:
            raise LobbyError("you are not in that lobby any more")
        member.seen = now
        return lobby, member

    def _new_code(self) -> str:
        for _ in range(64):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._lobbies:
                return code
        raise LobbyError("could not open a lobby; try again")

    # -- the verbs ----------------------------------------------------------

    def create(self, name: Any) -> dict[str, Any]:
        now = time.monotonic()
        self._sweep(now)
        if len(self._lobbies) >= MAX_LOBBIES:
            raise LobbyError("this server has no room for another lobby right now")
        lobby = Lobby(self._new_code())
        member = Member(secrets.token_urlsafe(16), _clean_name(name), now)
        lobby.members.append(member)
        self._lobbies[lobby.code] = lobby
        return {"token": member.token, "lobby": lobby.payload(member.token)}

    def join(self, code: str, name: Any) -> dict[str, Any]:
        now = time.monotonic()
        self._sweep(now)
        lobby = self._find(code)
        if len(lobby.members) >= MAX_MEMBERS:
            raise LobbyError("that lobby is full")
        member = Member(secrets.token_urlsafe(16), _clean_name(name), now)
        lobby.members.append(member)
        return {"token": member.token, "lobby": lobby.payload(member.token)}

    def state(self, code: str, token: str) -> dict[str, Any]:
        """The lobby as one member sees it. Asking is also the heartbeat."""
        now = time.monotonic()
        lobby, member = self._seated(code, token, now)
        self._sweep(now)
        return lobby.payload(member.token)

    def leave(self, code: str, token: str) -> bool:
        lobby = self._lobbies.get(str(code or "").strip().upper())
        if lobby is None:
            return False
        before = len(lobby.members)
        lobby.members = [m for m in lobby.members if m.token != token]
        if not lobby.members:
            self._lobbies.pop(lobby.code, None)
        return len(lobby.members) != before

    def play(
        self,
        code: str,
        token: str,
        find_match: Any,
        maps: list[str] | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """The leader looks for a match for everyone.

        `find_match` is `HassaultReferee.find_match`, handed in so this module
        holds no rooms of its own: the room chosen has a seat promised for
        every member, by the same rule a lone search follows.
        """
        now = time.monotonic()
        lobby, member = self._seated(code, token, now)
        self._sweep(now)
        if lobby.leader is not member:
            leader = lobby.leader
            raise LobbyError(
                f"only {leader.name if leader else 'the leader'} can start the search"
            )
        found = find_match(
            None, mode or None, maps=maps or None, seats=len(lobby.members)
        )
        lobby.maps = list(maps or [])
        lobby.mode = mode or ""
        lobby.serial += 1
        lobby.match = {
            "id": found["id"],
            "map": found["map"],
            "mode": found["mode"],
            "opened": found["opened"],
        }
        return lobby.payload(member.token)


book = LobbyBook()
