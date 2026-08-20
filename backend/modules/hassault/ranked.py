"""Ranked matches: this node relaying a client into the game server's room.

A rated match is simulated by the game server (`games_server/hassault_rooms.py`),
because a room inside a player's own backend cannot adjudicate that player. But
the browser and the native client should not have to know that: they speak one
protocol, the node's `hassault` channel, and the node decides where the room is.

So this is a **proxy**, exactly the shape `fabric.py` already uses for a match on
a friend's node:

```
client --/ws--> this node --wss--> game server (MatchRoom, referee)
```

Two reasons it is a proxy rather than the client connecting directly:

- **The token stays here.** The game-server JWT lives on the node
  (`server_auth`), the same one sign-in and `/game-ws` use. Handing it to a
  browser to open its own socket would spread the node's credential to every
  surface that wants to play.
- **One protocol.** The client's join differs by a single flag; everything after
  it — snapshots, inputs, the welcome — is the wire it already speaks. A second
  client-side transport is a second place for the two to drift.

The result comes back **from the server**, and that is the whole point: it is
recorded locally with `authority="server"` because the server said so, not
because this node computed anything.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

CHANNEL = "hassault"

#: How long to wait for the server's socket. A ranked join that hangs is worse
#: than one that fails: the menu has no way to say "still trying" and the player
#: is looking at a map that never loads.
CONNECT_TIMEOUT = 10.0

#: How long `leave` waits for the server's parting `result`. Short: the player is
#: already back in the menu, and a card that arrives is worth a beat while a menu
#: that hangs is not.
RESULT_WAIT = 2.0


def server_ws_url() -> str:
    """The rated endpoint on whichever game server this node plays against.

    Derived from the same `resolve_server_url` sign-in uses, never configured
    separately: a node that signed in to one server and played on another would
    have its JWT refused there, and the failure reads as "invalid token" rather
    than as two URLs that disagree.
    """
    from backend.modules.games.client import resolve_server_url

    base = resolve_server_url().rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :] + "/hassault-ws"
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :] + "/hassault-ws"
    return base + "/hassault-ws"


class RankedSession:
    """One client's proxied seat in a server-hosted match."""

    def __init__(self, conn: WsConnection) -> None:
        self.conn = conn
        self.socket: Any = None
        self.pump: asyncio.Task[None] | None = None
        self.room = ""
        self.map_name = ""
        #: Set once the server's parting `result` has been filed, so `leave` waits
        #: for the thing it is actually waiting for rather than for the socket to
        #: finish closing.
        self.result_seen = asyncio.Event()

    async def send(self, event: str, data: dict[str, Any]) -> None:
        """Up to the game server."""
        if self.socket is None:
            return
        try:
            await self.socket.send(
                json.dumps({"channel": CHANNEL, "event": event, "data": data})
            )
        except Exception:
            # The pump notices the close and tells the client; raising here would
            # only turn a disconnect into a traceback in the input path.
            pass


# Live ranked sessions, keyed the way `match_server` keys membership: by the
# identity of the client's socket object.
_sessions: dict[int, RankedSession] = {}


def session_for(conn: WsConnection) -> RankedSession | None:
    return _sessions.get(id(conn))


async def join(conn: WsConnection, map_name: str) -> None:
    """Open a socket to the game server and put this client in a rated room.

    Errors are reported **to the client on its own channel** rather than raised:
    the caller is a websocket handler, and a ranked join that fails should leave
    the player in the menu with a reason, not close their socket.
    """
    from websockets.asyncio.client import connect as ws_connect

    from backend.modules.games import server_auth

    await leave(conn)

    token = server_auth._play_token()
    url = f"{server_ws_url()}?token={quote(token, safe='')}"
    session = RankedSession(conn)
    try:
        session.socket = await asyncio.wait_for(
            ws_connect(url, max_size=2**20), timeout=CONNECT_TIMEOUT
        )
    except Exception as exc:
        logger.info("hassault: ranked join could not reach the game server: %s", exc)
        await conn.send_json(
            {
                "channel": CHANNEL,
                "event": "error",
                "data": {
                    "message": "could not reach the ranked server",
                    "code": "ranked_unreachable",
                },
            }
        )
        return

    _sessions[id(conn)] = session
    await session.send("join", {"map": map_name, "name": ""})
    # Detached: this pump delivers the welcome that the caller is *inside the
    # handler for*. Awaiting it here would deadlock the socket that has to
    # receive it — the same rule the other `/ws` relays follow.
    session.pump = asyncio.create_task(_pump(session))


async def _pump(session: RankedSession) -> None:
    """Everything the game server says, forwarded to the client verbatim.

    Verbatim on purpose: a snapshot reshaped on the way through is a second
    implementation of the wire, and the client already knows how to read the
    original. The two events this *does* look at are the ones the node has a
    stake in — the welcome (which room we ended up in) and the result (which
    becomes a row here).
    """
    socket = session.socket
    if socket is None:
        return
    try:
        async for raw in socket:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            event = str(msg.get("event") or "")
            data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
            if event == "welcome":
                session.room = str(data.get("room") or "")
                session.map_name = str(data.get("map") or "")
            elif event == "result":
                _record(data)
                session.result_seen.set()
            await session.conn.send_json(msg)
    except Exception as exc:
        logger.info("hassault: ranked session ended: %s", exc)
    finally:
        await _drop(session)


def _record(result: dict[str, Any]) -> None:
    """File a server-adjudicated match.

    `authority="server"` is passed because **the server said so** — this node
    computed none of it. That flag is the difference between a personal record and
    a result something comparative may be built on, so it is never inferred from
    "we were in a ranked room".
    """
    from backend.modules.games import server_auth
    from backend.modules.hassault import results

    account = server_auth.signed_in_account()
    account_id = str((account or {}).get("account_id") or "local_player")
    try:
        results.record(account_id, result, authority="server")
    except Exception:
        logger.exception("hassault: could not record a ranked result")


async def _drop(session: RankedSession) -> None:
    _sessions.pop(id(session.conn), None)
    if session.socket is not None:
        try:
            await session.socket.close()
        except Exception:
            pass
        session.socket = None


async def leave(conn: WsConnection) -> None:
    """Take this client out of its ranked room, if it is in one."""
    session = _sessions.pop(id(conn), None)
    if session is None:
        return
    await session.send("leave", {})
    # A short window for the server's `result` to arrive before the socket goes.
    # Cancelling the pump immediately is how a player's last match silently never
    # gets a card: the result is on the wire, and nothing is left reading it.
    if session.pump is not None:
        try:
            await asyncio.wait_for(session.result_seen.wait(), timeout=RESULT_WAIT)
        except (TimeoutError, asyncio.TimeoutError):
            # No result: the server dropped us, or this seat never played. Not
            # worth reporting — the card is the only thing missing.
            pass
        session.pump.cancel()
    if session.socket is not None:
        try:
            await session.socket.close()
        except Exception:
            pass
        session.socket = None


async def relay_respawn(conn: WsConnection) -> None:
    """Ask the server to put us back in. It decides whether we may be."""
    session = _sessions.get(id(conn))
    if session is not None:
        await session.send("respawn", {})


async def relay_input(conn: WsConnection, data: dict[str, Any]) -> None:
    """Forward one input message untouched.

    **Not validated here.** The server validates, because the server is the one
    being lied to; a second check on the way out would be a copy of the rules with
    no authority behind it — the same reasoning `fabric.py` gives for forwarding a
    guest's input verbatim to the host.
    """
    session = _sessions.get(id(conn))
    if session is not None:
        await session.send("input", data)
