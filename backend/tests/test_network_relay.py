"""The relay broker: authenticated registration, routing by `dst`, and the answers
that let a dial fail fast. See backend/modules/network/relay_broker.py."""

import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend.modules.network import identity
from backend.modules.network.relay_broker import _clients, app, challenge_bytes


def _key() -> identity.Identity:
    return identity.Identity(Ed25519PrivateKey.generate())


def _register(ws, key: identity.Identity) -> dict:
    challenge = json.loads(ws.receive_text())["challenge"]
    ws.send_text(
        json.dumps(
            {
                "register": key.node_id,
                "public_key": key.public_key,
                "sig": key.sign(challenge_bytes(challenge)),
            }
        )
    )
    return json.loads(ws.receive_text())


def test_broker_forwards_by_dst():
    a_key, b_key = _key(), _key()
    client = TestClient(app)
    with (
        client.websocket_connect("/relay-ws") as a,
        client.websocket_connect("/relay-ws") as b,
    ):
        assert _register(a, a_key)["registered"] == a_key.node_id
        assert _register(b, b_key)["registered"] == b_key.node_id

        frame = {
            "type": "hello",
            "src": a_key.node_id,
            "dst": b_key.node_id,
            "msg_id": "m1",
        }
        a.send_text(json.dumps(frame))
        got = json.loads(b.receive_text())
        assert got["src"] == a_key.node_id
        assert got["msg_id"] == "m1"


def test_a_frame_to_an_offline_node_is_answered_not_swallowed():
    """So a relay dial to someone offline fails at once, not after the timeout."""
    key = _key()
    client = TestClient(app)
    with client.websocket_connect("/relay-ws") as a:
        _register(a, key)
        a.send_text(json.dumps({"type": "hello", "src": key.node_id, "dst": "ghost"}))
        assert json.loads(a.receive_text()) == {"undeliverable": "ghost"}
        assert client.get("/health").json()["status"] == "ok"


def test_registration_must_be_proven_with_the_node_key():
    """Claiming a node id you cannot sign for used to be enough to receive that
    node's traffic. On a hosted broker every node uses, that is interception."""
    victim, attacker = _key(), _key()
    client = TestClient(app)
    with client.websocket_connect("/relay-ws") as ws:
        challenge = json.loads(ws.receive_text())["challenge"]
        # The victim's id and key, signed by the attacker.
        ws.send_text(
            json.dumps(
                {
                    "register": victim.node_id,
                    "public_key": victim.public_key,
                    "sig": attacker.sign(challenge_bytes(challenge)),
                }
            )
        )
        assert "error" in json.loads(ws.receive_text())
        # The attacker's key with the victim's id: fingerprint mismatch.
        ws.send_text(
            json.dumps(
                {
                    "register": victim.node_id,
                    "public_key": attacker.public_key,
                    "sig": attacker.sign(challenge_bytes(challenge)),
                }
            )
        )
        assert "error" in json.loads(ws.receive_text())
    assert victim.node_id not in _clients


def test_a_signature_over_another_challenge_is_refused():
    key = _key()
    client = TestClient(app)
    with client.websocket_connect("/relay-ws") as ws:
        ws.receive_text()
        ws.send_text(
            json.dumps(
                {
                    "register": key.node_id,
                    "public_key": key.public_key,
                    "sig": key.sign(challenge_bytes("a-replayed-challenge")),
                }
            )
        )
        assert "error" in json.loads(ws.receive_text())


def test_frames_are_pinned_to_the_registered_sender():
    a_key, b_key, c_key = _key(), _key(), _key()
    client = TestClient(app)
    with (
        client.websocket_connect("/relay-ws") as a,
        client.websocket_connect("/relay-ws") as b,
    ):
        _register(a, a_key)
        _register(b, b_key)
        # A claims to be C: dropped, not forwarded.
        a.send_text(
            json.dumps({"type": "hello", "src": c_key.node_id, "dst": b_key.node_id})
        )
        a.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "src": a_key.node_id,
                    "dst": b_key.node_id,
                    "msg_id": "real",
                }
            )
        )
        assert json.loads(b.receive_text())["msg_id"] == "real"


def test_an_unregistered_connection_cannot_send():
    a_key, b_key = _key(), _key()
    client = TestClient(app)
    with (
        client.websocket_connect("/relay-ws") as anon,
        client.websocket_connect("/relay-ws") as a,
        client.websocket_connect("/relay-ws") as b,
    ):
        anon.receive_text()
        _register(a, a_key)
        _register(b, b_key)
        anon.send_text(
            json.dumps({"type": "hello", "src": a_key.node_id, "dst": b_key.node_id})
        )
        a.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "src": a_key.node_id,
                    "dst": b_key.node_id,
                    "msg_id": "real",
                }
            )
        )
        # The first frame B sees is the registered sender's, not the anonymous one.
        assert json.loads(b.receive_text()).get("msg_id") == "real"


def test_the_game_server_hosts_the_same_authenticated_relay():
    """Nodes default to the game server's relay, so it must be this broker — with
    registration proven — not a copy that drifts."""
    from backend.games_server.app import app as game_app

    a_key, b_key, attacker = _key(), _key(), _key()
    client = TestClient(game_app)
    with (
        client.websocket_connect("/relay-ws") as a,
        client.websocket_connect("/relay-ws") as b,
    ):
        assert _register(a, a_key)["registered"] == a_key.node_id
        assert _register(b, b_key)["registered"] == b_key.node_id
        a.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "src": a_key.node_id,
                    "dst": b_key.node_id,
                    "msg_id": "g1",
                }
            )
        )
        assert json.loads(b.receive_text())["msg_id"] == "g1"

    with client.websocket_connect("/relay-ws") as ws:
        challenge = json.loads(ws.receive_text())["challenge"]
        ws.send_text(
            json.dumps(
                {
                    "register": a_key.node_id,
                    "public_key": a_key.public_key,
                    "sig": attacker.sign(challenge_bytes(challenge)),
                }
            )
        )
        assert "error" in json.loads(ws.receive_text())
    assert client.get("/relay/health").json()["status"] == "ok"


def test_the_brokers_crypto_matches_the_nodes():
    """The broker verifies with its own copy so the game server need not import the
    node's module graph. A drifted fingerprint would refuse every registration."""
    from backend.modules.network import relay_broker

    for _ in range(20):
        key = _key()
        assert relay_broker.node_fingerprint(key.public_key) == identity.fingerprint(
            key.public_key
        )
        payload = challenge_bytes("c")
        sig = key.sign(payload)
        assert relay_broker.verify_signature(key.public_key, payload, sig)
        assert identity.verify(key.public_key, payload, sig)
        assert not relay_broker.verify_signature(key.public_key, b"other", sig)


def test_the_game_server_does_not_import_the_node_graph():
    """`games_server` deploys on its own. Mounting the relay must not pull in the
    node's settings store (via `network.identity`)."""
    import subprocess
    import sys

    code = (
        "import sys, backend.games_server.app; "
        "bad = [m for m in ('backend.modules.network.identity',"
        " 'backend.modules.settings.routes') if m in sys.modules]; "
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]", out.stdout
