"""Bridge between a browser's `/ws` `network` channel and the process-global
`PeerHub`. Each `/ws` connection subscribes to hub events so presence updates fan
out live; inbound `network` messages drive the hub (list/connect/disconnect/pair).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.modules.network.hub import peer_hub
from backend.modules.network.models import ConnectRequest
from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "network", "event": event, "data": data}


def subscribe_conn(conn: WsConnection) -> object:
    """Fan hub peer/presence events out to one browser connection. Returns the
    unsubscribe handle the `/ws` loop calls on disconnect."""

    def cb(event: str, data: dict[str, Any]) -> None:
        # Hub emits synchronously; the socket send is async, so schedule it.
        asyncio.ensure_future(conn.send_json(_evt(event, data)))

    return peer_hub.subscribe(cb)


async def _connect(conn: WsConnection, data: dict[str, Any]) -> None:
    req = ConnectRequest.model_validate(data)
    if not req.address:
        await conn.send_json(_evt("error", {"message": "connect requires an address"}))
        return
    try:
        info = await peer_hub.connect(req.address, req.transport)
        await conn.send_json(_evt("peer_update", {"peer": info.model_dump()}))
    except Exception as exc:
        logger.info("peer connect failed: %s", exc)
        await conn.send_json(_evt("error", {"message": f"connect failed: {exc}"}))


async def handle_network_message(conn: WsConnection, msg: dict[str, Any]) -> None:
    """Route an inbound `network`-channel message from the browser."""
    event = msg.get("event")
    data = msg.get("data") or {}
    if event == "list_peers":
        await conn.send_json(_evt("peers", peer_hub.snapshot().model_dump()))
    elif event == "connect":
        # Dial + handshake can take a moment; don't block the receive loop.
        asyncio.create_task(_connect(conn, data))
    elif event == "disconnect":
        node_id = str(data.get("nodeId", ""))
        if node_id:
            await peer_hub.disconnect(node_id)
    elif event == "pair_redeem":
        asyncio.create_task(_pair_redeem(conn, str(data.get("invite", ""))))


async def _pair_redeem(conn: WsConnection, invite: str) -> None:
    from backend.modules.network import trust

    try:
        address, token = trust.parse_invite(invite)
    except Exception as exc:
        await conn.send_json(
            _evt("pair_result", {"ok": False, "error": f"bad invite: {exc}"})
        )
        return
    try:
        info = await peer_hub.connect(address, "direct", token=token)
        await conn.send_json(
            _evt("pair_result", {"ok": True, "peer": info.model_dump()})
        )
    except Exception as exc:
        await conn.send_json(_evt("pair_result", {"ok": False, "error": str(exc)}))
