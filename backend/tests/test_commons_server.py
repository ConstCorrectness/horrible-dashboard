"""Phase 1 of the agent commons: the standalone index server (signed profiles +
vectordb matchmaking). See docs/architecture/agent-commons.mdx."""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend.modules.network import commons, commons_server, identity, trust
from backend.modules.network.models import (
    CommonsProfile,
    canonical_profile_bytes,
    canonical_vouch_bytes,
)
from backend.modules.vectordb.embeddings import get_local_fallback_embedding


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))

    # Hermetic + deterministic: use the local hash embedding directly so tests never
    # reach for an LLM provider over the network.
    async def _fake_embed(text: str) -> tuple[list[float], str]:
        return get_local_fallback_embedding(text), "local-fallback"

    monkeypatch.setattr(commons_server, "get_embedding", _fake_embed)
    commons_server._profiles.clear()
    with TestClient(commons_server.app) as test_client:
        yield test_client


def _make_profile(
    display_name: str,
    headline: str = "",
    tags: list[str] | None = None,
    seeking: str = "",
    visibility: str = "public",
) -> tuple[identity.Identity, CommonsProfile]:
    key = identity.Identity(Ed25519PrivateKey.generate())
    profile = CommonsProfile(
        node_id=key.node_id,
        public_key=key.public_key,
        display_name=display_name,
        headline=headline,
        tags=tags or [],
        seeking=seeking,
        visibility=visibility,  # type: ignore[arg-type]
    )
    profile.sig = key.sign(canonical_profile_bytes(profile))
    return key, profile


def _publish(ws: Any, profile: CommonsProfile) -> dict[str, Any]:
    ws.send_json({"type": "publish_profile", "profile": profile.model_dump()})
    return ws.receive_json()


def test_publish_and_directory(client: TestClient) -> None:
    _, profile = _make_profile("Rusty", "rust systems programming", ["rust"])
    with client.websocket_connect("/commons-ws") as ws:
        published = _publish(ws, profile)
        assert published == {
            "type": "published",
            "ok": True,
            "node_id": profile.node_id,
        }

        ws.send_json({"type": "directory"})
        directory = ws.receive_json()
        assert directory["type"] == "directory"
        entries = {e["node_id"]: e for e in directory["profiles"]}
        assert profile.node_id in entries
        assert entries[profile.node_id]["status"] == "connected"
        assert entries[profile.node_id]["display_name"] == "Rusty"


def test_publish_rejects_tampered_signature(client: TestClient) -> None:
    _, profile = _make_profile("Mallory", "honest headline")
    profile.headline = "tampered after signing"  # invalidates the signature
    with client.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "publish_profile", "profile": profile.model_dump()})
        res = ws.receive_json()
    assert res["type"] == "error"
    assert res["code"] == "auth"


def test_publish_rejects_fingerprint_mismatch(client: TestClient) -> None:
    key = identity.Identity(Ed25519PrivateKey.generate())
    # A validly-signed profile, but claiming a node_id that isn't this key's fingerprint.
    profile = CommonsProfile(
        node_id="deadbeefdeadbeef",
        public_key=key.public_key,
        display_name="Imposter",
    )
    profile.sig = key.sign(canonical_profile_bytes(profile))
    with client.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "publish_profile", "profile": profile.model_dump()})
        res = ws.receive_json()
    assert res["type"] == "error"
    assert res["code"] == "auth"


def test_search_ranks_relevant_profile_first(client: TestClient) -> None:
    _, rust = _make_profile(
        "Rusty",
        "rust systems programming and data visualization dashboards",
        ["rust", "dataviz"],
    )
    _, cook = _make_profile(
        "Chef", "cooking recipes and home gardening tips", ["cooking", "garden"]
    )
    for profile in (rust, cook):
        with client.websocket_connect("/commons-ws") as ws:
            assert _publish(ws, profile)["ok"] is True

    with client.websocket_connect("/commons-ws") as ws:
        ws.send_json(
            {
                "type": "search",
                "query": "rust data visualization programming",
                "limit": 5,
            }
        )
        res = ws.receive_json()

    assert res["type"] == "candidates"
    ids = [r["profile"]["node_id"] for r in res["results"]]
    assert ids, "expected at least one candidate"
    assert ids[0] == rust.node_id
    assert cook.node_id in ids  # both indexed, rust just ranks higher


def test_unlisted_profile_excluded_from_search(client: TestClient) -> None:
    _, hidden = _make_profile(
        "Hidden", "kayaking and whitewater rafting", ["kayak"], visibility="unlisted"
    )
    with client.websocket_connect("/commons-ws") as ws:
        assert _publish(ws, hidden)["ok"] is True

    with client.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "search", "query": "kayaking rafting", "limit": 5})
        res = ws.receive_json()
    ids = [r["profile"]["node_id"] for r in res["results"]]
    assert hidden.node_id not in ids


def test_connect_request_accept(client: TestClient) -> None:
    _, alice = _make_profile("Alice", "rust")
    _, bob = _make_profile("Bob", "design")
    with (
        client.websocket_connect("/commons-ws") as wsa,
        client.websocket_connect("/commons-ws") as wsb,
    ):
        assert _publish(wsa, alice)["ok"] is True
        assert _publish(wsb, bob)["ok"] is True

        # Alice asks to meet Bob.
        wsa.send_json(
            {"type": "connect_request", "to_node_id": bob.node_id, "note": "hi"}
        )
        inbound = wsb.receive_json()
        assert inbound["type"] == "connect_request"
        assert inbound["from"]["node_id"] == alice.node_id
        assert inbound["note"] == "hi"
        request_id = inbound["request_id"]

        # Bob accepts — both sides told to link up, requester dials.
        wsb.send_json(
            {"type": "connect_response", "request_id": request_id, "accept": True}
        )
        a_connected = wsa.receive_json()
        b_connected = wsb.receive_json()
        assert a_connected["type"] == "connected"
        assert a_connected["dial"] is True
        assert a_connected["peer"]["node_id"] == bob.node_id
        assert b_connected["type"] == "connected"
        assert b_connected["dial"] is False
        assert b_connected["peer"]["node_id"] == alice.node_id


def test_connect_request_decline(client: TestClient) -> None:
    _, alice = _make_profile("Alice")
    _, bob = _make_profile("Bob")
    with (
        client.websocket_connect("/commons-ws") as wsa,
        client.websocket_connect("/commons-ws") as wsb,
    ):
        assert _publish(wsa, alice)["ok"] is True
        assert _publish(wsb, bob)["ok"] is True
        wsa.send_json({"type": "connect_request", "to_node_id": bob.node_id})
        request_id = wsb.receive_json()["request_id"]
        wsb.send_json(
            {"type": "connect_response", "request_id": request_id, "accept": False}
        )
        declined = wsa.receive_json()
        assert declined["type"] == "declined"
        assert declined["node_id"] == bob.node_id


def test_connect_request_offline_target(client: TestClient) -> None:
    _, alice = _make_profile("Alice")
    with client.websocket_connect("/commons-ws") as wsa:
        assert _publish(wsa, alice)["ok"] is True
        wsa.send_json({"type": "connect_request", "to_node_id": "ffffffffffffffff"})
        res = wsa.receive_json()
        assert res["type"] == "request_failed"
        assert res["reason"] == "offline"


def test_unpublish_removes_from_search(client: TestClient) -> None:
    _, profile = _make_profile("Ghost", "temporary listing about kayaking", ["kayak"])
    with client.websocket_connect("/commons-ws") as ws:
        assert _publish(ws, profile)["ok"] is True
        ws.send_json({"type": "unpublish"})
        assert ws.receive_json()["type"] == "unpublished"

    with client.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "search", "query": "kayaking", "limit": 5})
        res = ws.receive_json()
    ids = [r["profile"]["node_id"] for r in res["results"]]
    assert profile.node_id not in ids


def test_trust_tiers_and_annotation(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The node-side viewer-relative trust tier: blocked / known / vouched / unknown."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    cc = commons.CommonsClient()
    trust.save_known_peer("blockedone111", {"blocked": True})
    trust.save_known_peer("knownone22222", {"trusted": True})

    assert cc._tier("blockedone111") == "blocked"
    assert cc._tier("knownone22222") == "known"
    assert cc._tier("strangerxxxxx") == "unknown"

    # Vouched: a stranger vouched for by a node I already trust.
    assert cc._tier("strangerxxxxx", ["knownone22222"]) == "vouched"
    # A vouch from someone I don't trust doesn't count.
    assert cc._tier("strangerxxxxx", ["randovouchr00"]) == "unknown"
    # Blocked/known take precedence over vouches.
    assert cc._tier("blockedone111", ["knownone22222"]) == "blocked"

    annotated = cc._annotate(
        [
            {"node_id": "knownone22222"},
            {"node_id": "strangerxxxxx", "vouchers": ["knownone22222"]},
        ]
    )
    assert annotated[0]["trust_tier"] == "known"
    assert annotated[1]["trust_tier"] == "vouched"


def test_vouch_appears_in_directory(client: TestClient) -> None:
    a_key, alice = _make_profile("Alice")
    b_key, bob = _make_profile("Bob")
    with (
        client.websocket_connect("/commons-ws") as wsa,
        client.websocket_connect("/commons-ws") as wsb,
    ):
        assert _publish(wsa, alice)["ok"] is True
        assert _publish(wsb, bob)["ok"] is True
        sig = a_key.sign(canonical_vouch_bytes(a_key.node_id, b_key.node_id))
        wsa.send_json({"type": "vouch", "subject_node_id": b_key.node_id, "sig": sig})
        wsa.send_json({"type": "directory"})
        directory = wsa.receive_json()
    assert directory["type"] == "directory"
    bob_entry = next(p for p in directory["profiles"] if p["node_id"] == b_key.node_id)
    assert a_key.node_id in bob_entry["vouchers"]


def test_vouch_rejects_bad_signature(client: TestClient) -> None:
    a_key, alice = _make_profile("Alice")
    b_key, bob = _make_profile("Bob")
    with (
        client.websocket_connect("/commons-ws") as wsa,
        client.websocket_connect("/commons-ws") as wsb,
    ):
        assert _publish(wsa, alice)["ok"] is True
        assert _publish(wsb, bob)["ok"] is True
        wsa.send_json(
            {"type": "vouch", "subject_node_id": b_key.node_id, "sig": "bm90LWEtc2ln"}
        )
        wsa.send_json({"type": "directory"})
        directory = wsa.receive_json()
    bob_entry = next(p for p in directory["profiles"] if p["node_id"] == b_key.node_id)
    assert a_key.node_id not in bob_entry.get("vouchers", [])
