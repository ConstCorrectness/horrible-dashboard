"""REST surface tests for the network module: identity, invite minting, and the
peer snapshot. The peer fabric starts via the app lifespan."""

from fastapi.testclient import TestClient

from backend.app import app


def test_identity_endpoint():
    with TestClient(app) as client:
        res = client.get("/api/network/identity")
        assert res.status_code == 200
        body = res.json()
        assert len(body["node_id"]) == 16
        assert body["public_key"]
        assert "agent" in body["capabilities"]


def test_peers_starts_empty():
    with TestClient(app) as client:
        res = client.get("/api/network/peers")
        assert res.status_code == 200
        body = res.json()
        assert body["peers"] == []
        assert body["self"]["node_id"]


def test_invite_roundtrips():
    with TestClient(app) as client:
        res = client.post("/api/network/invite", json={})
        assert res.status_code == 200
        body = res.json()
        assert body["invite"] and body["token"]
        # The invite decodes back to an address + the same token.
        from backend.modules.network import trust

        address, token = trust.parse_invite(body["invite"])
        assert token == body["token"]
        assert address.endswith("/peer-ws")


def test_pair_with_bad_invite_is_400():
    with TestClient(app) as client:
        res = client.post("/api/network/pair", json={"invite": "not-base64!!"})
        assert res.status_code == 400


def test_connect_requires_address():
    with TestClient(app) as client:
        res = client.post("/api/network/connect", json={"transport": "direct"})
        assert res.status_code == 400


def test_ask_peer_unknown_peer_returns_error():
    with TestClient(app) as client:
        res = client.post(
            "/api/network/ask-peer", json={"peer_id": "nope", "prompt": "hi"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is False
        assert "no connected peer" in body["error"]
