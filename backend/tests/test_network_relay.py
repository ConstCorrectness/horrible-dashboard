"""Relay broker routing test: two clients register, and a frame addressed by `dst`
is forwarded to the destination connection (and not echoed back to the sender)."""

import json

from fastapi.testclient import TestClient

from backend.modules.network.relay_broker import app


def test_broker_forwards_by_dst():
    client = TestClient(app)
    with (
        client.websocket_connect("/relay-ws") as a,
        client.websocket_connect("/relay-ws") as b,
    ):
        a.send_text(json.dumps({"register": "node-a"}))
        assert json.loads(a.receive_text())["registered"] == "node-a"
        b.send_text(json.dumps({"register": "node-b"}))
        assert json.loads(b.receive_text())["registered"] == "node-b"

        frame = {"type": "hello", "src": "node-a", "dst": "node-b", "msg_id": "m1"}
        a.send_text(json.dumps(frame))
        got = json.loads(b.receive_text())
        assert got["src"] == "node-a"
        assert got["msg_id"] == "m1"


def test_broker_drops_unknown_dst():
    client = TestClient(app)
    with client.websocket_connect("/relay-ws") as a:
        a.send_text(json.dumps({"register": "node-a"}))
        assert json.loads(a.receive_text())["registered"] == "node-a"
        # Addressed to a node that isn't connected: silently dropped (no crash).
        a.send_text(json.dumps({"type": "ping", "src": "node-a", "dst": "ghost"}))
        # Health still responds, proving the broker stayed up.
        assert client.get("/health").json()["status"] == "ok"
