"""Viewer chat: one broadcast room per token, held in memory and never stored.

Chat on a public share is the one thing viewers can *send*, which makes it the
one place a stranger's input reaches other people. So the rules are deliberately
blunt:

- **Nothing is persisted.** A share link is ephemeral and the conversation on it
  is too. There is no history to fetch on join, which also means there is no
  backlog to leak to whoever opens the link an hour later.
- **Nothing is trusted.** A name and a body are length-capped here and rendered
  with `textContent` on the page. The relay never interprets either.
- **A name is a label, not an identity.** Two viewers may both call themselves
  the host's name and the relay has no way to tell -- the same rule the fabric
  states about `node_name`. The viewer page says viewers can watch and chat, and
  never presents a chat name as authenticated.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque

from fastapi import WebSocket

logger = logging.getLogger(__name__)

MAX_NAME = 24
MAX_TEXT = 500

#: Per-connection rate limit. Generous for a human, hostile to a script.
BURST = 5
WINDOW_S = 10.0


class Room:
    """Everyone connected to one token's chat."""

    def __init__(self) -> None:
        self.sockets: set[WebSocket] = set()
        self._sent: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=BURST))

    async def join(self, ws: WebSocket) -> None:
        self.sockets.add(ws)
        await self.broadcast(
            {"kind": "system", "text": f"{len(self.sockets)} watching"}
        )

    async def leave(self, ws: WebSocket) -> None:
        self.sockets.discard(ws)
        self._sent.pop(id(ws), None)
        await self.broadcast(
            {"kind": "system", "text": f"{len(self.sockets)} watching"}
        )

    def allowed(self, ws: WebSocket, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        stamps = self._sent[id(ws)]
        if len(stamps) == BURST and now - stamps[0] < WINDOW_S:
            return False
        stamps.append(now)
        return True

    async def broadcast(self, message: dict[str, object]) -> None:
        payload = json.dumps(message)
        for ws in list(self.sockets):
            try:
                await ws.send_text(payload)
            except Exception:
                # A socket that died between the iteration and the send is not an
                # error worth logging per-message; the disconnect handler sweeps it.
                self.sockets.discard(ws)


class Chat:
    """Every chat room on this process."""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def room(self, token: str) -> Room:
        room = self._rooms.get(token)
        if room is None:
            room = Room()
            self._rooms[token] = room
        return room

    def drop(self, token: str) -> None:
        self._rooms.pop(token, None)

    def __len__(self) -> int:
        return len(self._rooms)


def clean(raw: object, limit: int) -> str:
    """Coerce untrusted JSON into a bounded single-line string.

    Newlines go because a multi-line name turns one chat row into a wall, which
    is the cheapest possible way to shout over everyone else.
    """
    text = raw if isinstance(raw, str) else ""
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


def parse(raw: str) -> dict[str, str] | None:
    """A viewer's frame, or None if it is not usable."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    text = clean(data.get("text"), MAX_TEXT)
    if not text:
        return None
    return {"name": clean(data.get("name"), MAX_NAME) or "guest", "text": text}
