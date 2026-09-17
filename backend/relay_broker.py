"""Rendezvous broker for the relay transport — how two nodes behind different NATs
reach each other.

Nodes connect to `/relay-ws`, **prove** which node they are, then send signed
`PeerEnvelope`s addressed by `dst`; the broker forwards each frame to the
destination's connection. It never alters payloads and cannot forge them — they are
end-to-end signed by node keys the broker does not hold. It *can* read them: TLS ends
at the broker, and envelopes are signed, not encrypted.

## Registration is authenticated

The broker used to accept `{"register": "<any node id>"}` from anyone. Harmless on a
private broker; on the hosted one every node uses by default, it would let anyone
claim a friend's node id and have that friend's traffic delivered to them instead.
So a connection is first sent a random `challenge`, and registers by returning
`{register, public_key, sig}` where `node_id` is the fingerprint of `public_key`
and `sig` covers the challenge — the same self-certifying rule the handshake uses.
A later registration for the same node id replaces the earlier connection, which
is what a reconnect looks like.

## Frames are pinned to their sender

A forwarded frame's `src` must be the node the connection registered as. The hub
would reject a forged `src` anyway (signature check against the session key), but
checking here costs nothing and stops a connection from spinning up links in
someone else's name.

## A frame to nobody is answered

`{"undeliverable": dst}` goes back when `dst` is not connected, so a dial to an
offline node fails in milliseconds instead of waiting out the handshake timeout.

Mounted by the game server (`backend/games_server/app.py`), which every node already
reaches, and still runnable on its own:

    uv run uvicorn backend.relay_broker:app --port 9000
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

app = FastAPI(title="horrible-dashboard relay broker")

# node_id -> connected WebSocket. A single process map; horizontal scaling would
# need a shared bus.
_clients: dict[str, WebSocket] = {}


def challenge_bytes(challenge: str) -> bytes:
    """What a registering node signs. Namespaced so a relay signature can never be
    replayed as anything else a node key signs."""
    return f"horrible.relay.register:{challenge}".encode()


def node_fingerprint(public_key_b64: str) -> str:
    """`base32(sha256(pubkey))[:16]`, the node id scheme.

    Self-contained on purpose, with `verify_signature` below: the game server mounts
    this broker and must not import the node's module graph (`network.identity`
    pulls in the settings store and the data directory). The duplication is pinned
    against `network.identity` by `test_network_relay.py`, the same way the game
    server's own crypto is pinned.
    """
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()[:16]


def verify_signature(public_key_b64: str, payload: bytes, signature_b64: str) -> bool:
    """Ed25519 verify. Never raises: attacker-supplied strings collapse to False."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64), payload)
        return True
    except Exception:  # noqa: BLE001
        return False


def verify_registration(challenge: str, msg: dict) -> str | None:
    """The node id a registration proves, or None."""
    node_id = msg.get("register")
    public_key = msg.get("public_key")
    sig = msg.get("sig")
    if not all(isinstance(v, str) and v for v in (node_id, public_key, sig)):
        return None
    try:
        if node_fingerprint(public_key) != node_id:
            return None
    except Exception:  # noqa: BLE001 - a malformed key is simply not a proof
        return None
    if not verify_signature(public_key, challenge_bytes(challenge), sig):
        return None
    return node_id


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "clients": len(_clients)}


async def relay_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    challenge = secrets.token_urlsafe(24)
    await websocket.send_text(json.dumps({"challenge": challenge}))
    node_id: str | None = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "register" in msg:
                proven = verify_registration(challenge, msg)
                if proven is None:
                    await websocket.send_text(
                        json.dumps({"error": "registration not proven"})
                    )
                    continue
                previous = _clients.get(proven)
                node_id = proven
                _clients[node_id] = websocket
                if previous is not None and previous is not websocket:
                    try:
                        await previous.close()
                    except Exception:  # noqa: BLE001
                        pass
                logger.info("relay register: %s (%d clients)", node_id, len(_clients))
                await websocket.send_text(json.dumps({"registered": node_id}))
                continue
            if node_id is None or msg.get("src") != node_id:
                continue
            dst = msg.get("dst")
            target = _clients.get(dst) if isinstance(dst, str) else None
            if target is None:
                await websocket.send_text(json.dumps({"undeliverable": dst}))
                continue
            try:
                await target.send_text(raw)
            except Exception:  # noqa: BLE001 - the target went away mid-send
                await websocket.send_text(json.dumps({"undeliverable": dst}))
    except WebSocketDisconnect:
        pass
    finally:
        if node_id and _clients.get(node_id) is websocket:
            del _clients[node_id]
            logger.info("relay drop: %s (%d clients)", node_id, len(_clients))


app.add_api_websocket_route("/relay-ws", relay_ws)
