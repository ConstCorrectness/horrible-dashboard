"""Publishing a trajectory to the agent commons: the digest, the confirmation, the index.

See backend/modules/trajectories/commons.py and the digest half of commons_server.py.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend.modules.network import commons_server, identity
from backend.modules.network.models import (
    CommonsTrajectoryDigest,
    canonical_digest_bytes,
    digest_content_id,
)
from backend.modules.trajectories import commons, store
from backend.modules.trajectories.models import StepWrite

SECRET = "sk-live-0123456789abcdefghijklmnop"


class FakeHub:
    def __init__(self) -> None:
        self.signer = identity.Identity(Ed25519PrivateKey.generate())

    def identity(self) -> Any:
        return self.signer


@pytest.fixture()
def hub(monkeypatch):
    store._initialized.clear()
    store.init_trajectories_db()
    fake = FakeHub()
    import backend.modules.network.hub as hub_mod

    monkeypatch.setattr(hub_mod, "peer_hub", fake)
    return fake


def _run(*, goal: str = f"deploy with key {SECRET}", status: str = "complete") -> str:
    if store.get_dataset("mine") is None:
        store.create_dataset("mine", "Mine")
    run_id = store.start_run(
        "mine", goal=goal, turn_id="private-turn", external_id="ext-1"
    )
    store.append_step(
        run_id,
        StepWrite(
            kind="action",
            round=0,
            name="files.read",
            args={"path": "/home/me/secrets.txt", "api_key": SECRET},
            result={"text": "the user's own words"},
            ok=True,
            duration_ms=12,
        ),
    )
    store.append_step(
        run_id, StepWrite(kind="message", role="assistant", content="private prose")
    )
    if status != "running":
        store.finish_run(run_id, status=status)
    return run_id


# ------------------------------------------------------------------- the digest


def test_a_digest_carries_the_shape_and_no_payload(hub):
    run_id = _run()
    digest = commons.build_digest(run_id)
    wire = json.dumps(digest.model_dump())

    assert [s.name for s in digest.steps] == ["files.read", ""]
    assert digest.steps[0].ok is True and digest.steps[0].duration_ms == 12
    for leaked in (SECRET, "secrets.txt", "the user's own words", "private prose"):
        assert leaked not in wire
    for private in ("private-turn", "ext-1", run_id):
        assert private not in wire
    # The goal is out unless asked for.
    assert digest.goal is None


def test_an_opted_in_goal_is_scrubbed(hub):
    digest = commons.build_digest(_run(), include_goal=True)
    assert digest.goal and "deploy with key" in digest.goal
    assert SECRET not in digest.goal


def test_the_same_run_hashes_the_same_whenever_it_is_published(hub):
    run_id = _run()
    a = commons.build_digest(run_id)
    b = commons.build_digest(run_id)
    b.published_at = 12345.0
    assert digest_content_id(a) == digest_content_id(b) == a.digest_id


def test_running_and_friend_runs_are_refused(hub):
    with pytest.raises(commons.PublishRefused, match="in progress"):
        commons.build_digest(_run(status="running"))

    store.create_dataset("peer-x", "From x", source_kind="peer")
    peer_run = store.start_run("peer-x", goal="theirs", source="peer")
    store.finish_run(peer_run, status="complete")
    with pytest.raises(commons.PublishRefused, match="not yours"):
        commons.build_digest(peer_run)


# ------------------------------------------------------------- the confirmation


class FakeCommons:
    connected = True

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def publish_trajectory(self, digest: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(digest)
        return {"type": "trajectory_published", "duplicate": False}


@pytest.fixture()
def index(monkeypatch):
    fake = FakeCommons()
    import backend.modules.network.commons as commons_mod

    monkeypatch.setattr(commons_mod, "commons_client", fake)
    return fake


def test_publishing_needs_the_code_for_this_exact_content(hub, index):
    run_id = _run()
    code = commons.preview(run_id)["confirm"]

    with pytest.raises(commons.PublishRefused, match="does not match"):
        asyncio.run(commons.publish(run_id, include_goal=False, confirm="nope"))
    # A code confirmed for the goal-less digest does not publish the goal.
    with pytest.raises(commons.PublishRefused, match="does not match"):
        asyncio.run(commons.publish(run_id, include_goal=True, confirm=code))
    assert index.sent == []

    out = asyncio.run(commons.publish(run_id, include_goal=False, confirm=code))
    assert len(index.sent) == 1
    sent = CommonsTrajectoryDigest.model_validate(index.sent[0])
    assert out["digest_id"] == sent.digest_id
    # What went out verifies against the index's own check.
    assert commons_server.verify_digest(sent) is None


def test_a_run_that_changed_after_the_preview_is_not_published(hub, index):
    run_id = _run()
    code = commons.preview(run_id)["confirm"]
    store.finish_run(run_id, status="failed")
    with pytest.raises(commons.PublishRefused, match="does not match"):
        asyncio.run(commons.publish(run_id, include_goal=False, confirm=code))
    assert index.sent == []


def test_no_index_is_a_reason_not_a_hang(hub, index):
    index.connected = False
    run_id = _run()
    code = commons.preview(run_id)["confirm"]
    with pytest.raises(commons.PublishRefused, match="not connected"):
        asyncio.run(commons.publish(run_id, include_goal=False, confirm=code))


# -------------------------------------------------------------------- the index


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    commons_server._profiles.clear()
    commons_server._digests.clear()
    with TestClient(commons_server.app) as client:
        yield client


def _signed(key: identity.Identity, **fields: Any) -> CommonsTrajectoryDigest:
    digest = CommonsTrajectoryDigest(
        node_id=key.node_id,
        public_key=key.public_key,
        status="complete",
        rounds=1,
        **fields,
    )
    digest.digest_id = digest_content_id(digest)
    digest.published_at = 1.0
    digest.sig = key.sign(canonical_digest_bytes(digest))
    return digest


def _key() -> identity.Identity:
    return identity.Identity(Ed25519PrivateKey.generate())


def test_index_accepts_dedupes_and_lists(server):
    key = _key()
    digest = _signed(key, model="qwen3:8b")
    with server.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "publish_trajectory", "digest": digest.model_dump()})
        assert ws.receive_json() == {
            "type": "trajectory_published",
            "digest_id": digest.digest_id,
            "duplicate": False,
        }
        again = digest.model_copy()
        again.published_at = 2.0
        again.sig = key.sign(canonical_digest_bytes(again))
        ws.send_json({"type": "publish_trajectory", "digest": again.model_dump()})
        assert ws.receive_json()["duplicate"] is True

        ws.send_json({"type": "list_trajectories"})
        listed = ws.receive_json()["digests"]
        assert [d["digest_id"] for d in listed] == [digest.digest_id]


@pytest.mark.parametrize("tamper", ["content", "id", "key"])
def test_index_refuses_a_digest_that_does_not_verify(server, tamper):
    key = _key()
    digest = _signed(key, model="m")
    if tamper == "content":
        digest.model = "something else"  # id and signature no longer cover this
    elif tamper == "id":
        digest.model = "something else"
        digest.digest_id = digest_content_id(digest)  # re-hashed, not re-signed
    else:
        digest.public_key = _key().public_key
    with server.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "publish_trajectory", "digest": digest.model_dump()})
        reply = ws.receive_json()
    assert reply["type"] == "error" and reply["code"] == "auth"
    assert reply["ref"] == digest.digest_id
    assert commons_server._digests == {}


def test_only_the_publisher_can_withdraw(server, monkeypatch):
    from backend.modules.network.models import CommonsProfile, canonical_profile_bytes

    owner, stranger = _key(), _key()
    digest = _signed(owner)

    def profile(key: identity.Identity) -> dict[str, Any]:
        p = CommonsProfile(
            node_id=key.node_id, public_key=key.public_key, display_name="n"
        )
        p.sig = key.sign(canonical_profile_bytes(p))
        return p.model_dump()

    async def no_embed(text: str) -> tuple[list[float], str]:
        from backend.modules.database.embeddings import get_local_fallback_embedding

        return get_local_fallback_embedding(text), "local-fallback"

    monkeypatch.setattr(commons_server, "get_embedding", no_embed)
    with server.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "publish_trajectory", "digest": digest.model_dump()})
        ws.receive_json()

    with server.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "publish_profile", "profile": profile(stranger)})
        ws.receive_json()
        ws.send_json({"type": "unpublish_trajectory", "digest_id": digest.digest_id})
        assert ws.receive_json()["ok"] is False
    assert digest.digest_id in commons_server._digests

    with server.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "publish_profile", "profile": profile(owner)})
        ws.receive_json()
        ws.send_json({"type": "unpublish_trajectory", "digest_id": digest.digest_id})
        assert ws.receive_json()["ok"] is True
    assert commons_server._digests == {}


def test_index_refuses_past_the_per_node_limit(server, monkeypatch):
    monkeypatch.setattr(commons_server, "DIGESTS_PER_NODE", 2)
    key = _key()
    with server.websocket_connect("/commons-ws") as ws:
        replies = []
        for n in range(3):
            d = _signed(key, model=f"m{n}")
            ws.send_json({"type": "publish_trajectory", "digest": d.model_dump()})
            replies.append(ws.receive_json())
    assert [r["type"] for r in replies] == [
        "trajectory_published",
        "trajectory_published",
        "error",
    ]
    assert replies[2]["code"] == "limit"


def test_a_validly_signed_digest_cannot_squat_another_digests_id(server):
    """The signature covers `digest_id`, so it verifies — but if the index trusted the
    id, a publisher could claim someone else's content hash first, and the real digest
    would then be answered `duplicate` and never stored."""
    victim = _signed(_key(), model="honest")
    squatter_key = _key()
    squatter = CommonsTrajectoryDigest(
        node_id=squatter_key.node_id,
        public_key=squatter_key.public_key,
        model="squat",
        digest_id=victim.digest_id,
        published_at=1.0,
    )
    squatter.sig = squatter_key.sign(canonical_digest_bytes(squatter))
    with server.websocket_connect("/commons-ws") as ws:
        ws.send_json({"type": "publish_trajectory", "digest": squatter.model_dump()})
        assert ws.receive_json()["code"] == "auth"
        ws.send_json({"type": "publish_trajectory", "digest": victim.model_dump()})
        assert ws.receive_json()["duplicate"] is False
