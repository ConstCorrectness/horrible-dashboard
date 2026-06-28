"""Lobby server tests: identity-checked register, room create/list/join, the
host/guest hand-off, and join authorization. Uses Starlette's websocket TestClient;
each node is a fresh Ed25519 identity."""

import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend.modules.network import identity
from backend.modules.network.lobby_server import app


def _make_node(name: str):
    priv = Ed25519PrivateKey.generate()
    pub = identity.encode_public(priv.public_key())
    node_id = identity.fingerprint(pub)
    sig = base64.b64encode(priv.sign(node_id.encode())).decode()
    register = {
        "type": "register",
        "node_id": node_id,
        "public_key": pub,
        "node_name": name,
        "addresses": [f"ws://{name}/peer-ws"],
        "capabilities": ["agent", "collab"],
        "sig": sig,
    }
    return node_id, pub, sig, register


def _recv_until(ws, mtype, limit=10):
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg.get("type") == mtype:
            return msg
    raise AssertionError(f"did not receive {mtype}")


def test_register_requires_valid_signature():
    _, pub, _, reg = _make_node("a")
    reg["sig"] = base64.b64encode(b"not a real signature").decode()
    with TestClient(app).websocket_connect("/lobby-ws") as ws:
        ws.send_text(json.dumps(reg))
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error"
        assert msg["code"] == "auth"


def test_create_and_list_room():
    _, _, _, reg = _make_node("host")
    with TestClient(app).websocket_connect("/lobby-ws") as ws:
        ws.send_text(json.dumps(reg))
        assert _recv_until(ws, "registered")
        ws.send_text(json.dumps({"type": "create_room", "name": "My Room"}))
        created = _recv_until(ws, "room_created")
        assert created["room"]["name"] == "My Room"
        ws.send_text(json.dumps({"type": "list_rooms"}))
        rooms = _recv_until(ws, "rooms")["rooms"]
        assert any(r["name"] == "My Room" for r in rooms)


def test_join_room_hands_off_host_and_guest():
    host_id, _, _, host_reg = _make_node("host")
    guest_id, _, _, guest_reg = _make_node("guest")
    client = TestClient(app)
    with (
        client.websocket_connect("/lobby-ws") as host,
        client.websocket_connect("/lobby-ws") as guest,
    ):
        host.send_text(json.dumps(host_reg))
        _recv_until(host, "registered")
        host.send_text(json.dumps({"type": "create_room", "name": "R"}))
        room = _recv_until(host, "room_created")["room"]

        guest.send_text(json.dumps(guest_reg))
        _recv_until(guest, "registered")
        guest.send_text(json.dumps({"type": "join_room", "roomId": room["id"]}))

        # Guest gets the host's reachable candidates...
        info = _recv_until(guest, "room_info")
        assert info["host"]["node_id"] == host_id
        assert info["host"]["addresses"] == ["ws://host/peer-ws"]
        # ...and the host is told who's joining.
        joining = _recv_until(host, "peer_joining")
        assert joining["guest"]["node_id"] == guest_id


def test_join_room_token_policy():
    _, _, _, host_reg = _make_node("host")
    _, _, _, guest_reg = _make_node("guest")
    client = TestClient(app)
    with (
        client.websocket_connect("/lobby-ws") as host,
        client.websocket_connect("/lobby-ws") as guest,
    ):
        host.send_text(json.dumps(host_reg))
        _recv_until(host, "registered")
        host.send_text(
            json.dumps(
                {
                    "type": "create_room",
                    "name": "Locked",
                    "joinPolicy": "token",
                    "token": "secret",
                }
            )
        )
        room = _recv_until(host, "room_created")["room"]

        guest.send_text(json.dumps(guest_reg))
        _recv_until(guest, "registered")
        # Wrong token → denied.
        guest.send_text(
            json.dumps({"type": "join_room", "roomId": room["id"], "token": "nope"})
        )
        err = _recv_until(guest, "error")
        assert err["code"] == "denied"
        # Right token → room_info.
        guest.send_text(
            json.dumps({"type": "join_room", "roomId": room["id"], "token": "secret"})
        )
        assert _recv_until(guest, "room_info")
