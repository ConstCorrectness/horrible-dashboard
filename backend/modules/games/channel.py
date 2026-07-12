"""The browser-facing `/ws` `games` channel.

Browser panels drive the node's `GameServerClient` (connect, lobby ops) and receive
relayed server events (table/tables/your_turn/public_state/game_over) for rendering.
This is a thin adapter: it translates the browser envelope `{channel,event,data}`
into `GameServerClient` calls, and the client relays events back the same way.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.games.client import games_client

logger = logging.getLogger(__name__)


async def handle_games_message(conn: Any, msg: dict[str, Any]) -> None:
    event = msg.get("event")
    data = msg.get("data") or {}

    # Any interaction subscribes this socket to relayed game events.
    games_client.subscribe(conn)

    try:
        if event == "connect":
            await games_client.connect(self_play=bool(data.get("selfPlay")))
        elif event == "disconnect":
            await games_client.disconnect()
        elif event == "list_tables":
            await games_client.list_tables()
        elif event == "create_table":
            await games_client.create_table(str(data.get("gameId") or ""))
        elif event == "join_table":
            await games_client.join_table(str(data.get("tableId") or ""))
        elif event == "leave_table":
            await games_client.leave_table(str(data.get("tableId") or ""))
        elif event == "run_challenges":
            await games_client.run_challenges(str(data.get("gameId") or "tictactoe"))
        elif event == "town_join":
            await games_client.town_join(
                str(data.get("name") or ""), str(data.get("avatar") or "")
            )
        elif event == "town_leave":
            await games_client.town_leave()
        elif event == "town_whisper":
            games_client.town_whisper(str(data.get("text") or ""))
        # ---- The Plaza (human social layer) ----
        elif event == "social_join":
            await games_client.social_join(
                str(data.get("name") or ""), str(data.get("avatar") or "")
            )
        elif event == "social_leave":
            await games_client.social_leave()
        elif event == "social_move":
            await games_client.social_move(
                float(data.get("x", 0.0)), float(data.get("y", 0.0))
            )
        elif event == "social_room":
            await games_client.social_room(str(data.get("room") or ""))
        elif event == "social_say":
            await games_client.social_say(
                str(data.get("text") or ""), bool(data.get("emote"))
            )
        elif event == "social_invite":
            await games_client.social_invite(
                str(data.get("account_id") or ""), str(data.get("gameId") or "")
            )
        elif event in ("friend_request", "friend_accept", "friend_remove"):
            await games_client.friend_action(
                event.removeprefix("friend_"), str(data.get("account_id") or "")
            )
        elif event == "friend_list":
            await games_client.friend_list()
        elif event == "profile_get":
            await games_client.profile_get()
        elif event == "profile_set":
            await games_client.profile_set(data.get("avatar"), data.get("bio"))
        else:
            logger.debug("games: ignoring unknown event %r", event)
    except Exception as e:
        logger.exception("games channel event %r failed", event)
        try:
            # Let the browser know we are not connected so UI can reset state
            await conn.send_json(
                {
                    "channel": "games",
                    "event": "authed",
                    "data": {
                        "type": "authed",
                        "connected": False,
                        "account_id": None,
                    },
                }
            )
            # Send the error message for toast display
            await conn.send_json(
                {
                    "channel": "games",
                    "event": "error",
                    "data": {
                        "message": str(e),
                        "event": event,
                    },
                }
            )
        except Exception:
            logger.exception("failed to send games channel error back to browser")


def drop_games_conn(conn: Any) -> None:
    """Stop relaying to a browser socket that has closed."""
    games_client.unsubscribe(conn)
