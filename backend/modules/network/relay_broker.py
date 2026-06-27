"""Standalone rendezvous broker for the relay transport.

A tiny, stateless forwarder: nodes connect to `/relay-ws`, register their node_id,
then send signed `PeerEnvelope`s addressed by `dst`; the broker forwards each frame
to the destination's connection. It never inspects or alters envelope payloads (it
can't — they're end-to-end signed), so a relay operator can route traffic without
being able to read or forge it.

Run separately from a node's own backend:

    uv run uvicorn backend.modules.network.relay_broker:app --port 9000
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

app = FastAPI(title="horrible-dashboard relay broker")

# node_id -> connected WebSocket. A single process map; horizontal scaling would
# need a shared bus, out of scope for this groundwork.
_clients: dict[str, WebSocket] = {}


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "clients": len(_clients)}


@app.websocket("/relay-ws")
async def relay_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    node_id: str | None = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            register = msg.get("register")
            if isinstance(register, str):
                node_id = register
                _clients[node_id] = websocket
                logger.info("relay register: %s (%d clients)", node_id, len(_clients))
                # Ack so a client can sequence sends after registration.
                await websocket.send_text(json.dumps({"registered": node_id}))
                continue
            dst = msg.get("dst")
            target = _clients.get(dst) if isinstance(dst, str) else None
            if target is not None:
                await target.send_text(raw)
    except WebSocketDisconnect:
        pass
    finally:
        if node_id and _clients.get(node_id) is websocket:
            del _clients[node_id]
            logger.info("relay drop: %s (%d clients)", node_id, len(_clients))
