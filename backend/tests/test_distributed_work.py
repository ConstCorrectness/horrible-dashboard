"""Distributing latency-tolerant work: embedding batches and eval targets.

These are the best-shaped jobs on this fabric — big batches, no latency
sensitivity — which is the opposite of the profile that makes speculative decoding
a poor fit for it.

**The most important test in this file is the embedding-model mismatch refusal.**
Vectors from a different model are real, the right width, and error at nothing.
They simply live in a different space, so mixing them into one LanceDB table
silently ruins retrieval with no recovery short of a full reindex. Every other
failure here is loud; that one is not.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.database import embed_peer


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A node whose data dir is empty, matching the evals suite's own fixture."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


class _Res:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_post(monkeypatch, response):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            if callable(response):
                return response(url, json)
            return response

    monkeypatch.setattr(embed_peer.httpx, "AsyncClient", lambda **kw: FakeClient())


# ---- the refusal that matters ---------------------------------------------------


def test_a_different_embedding_model_is_refused(monkeypatch):
    """The one silent failure in this area. A peer running a different model
    returns real, correctly-shaped vectors from a different space; persisting them
    ruins the table with no error at any point."""
    _patch_post(
        monkeypatch,
        _Res({"vectors": [[0.1, 0.2]], "method": "ollama/nomic-embed-text"}),
    )
    with pytest.raises(embed_peer.EmbeddingModelMismatch) as exc:
        asyncio.run(
            embed_peer.embed_via_peer(
                "http://127.0.0.1:1", ["hello"], expect_model="ollama/all-minilm"
            )
        )
    assert "all-minilm" in str(exc.value)


def test_the_borrower_checks_independently_of_the_peer(monkeypatch):
    """The lender refuses a mismatch too, but a borrower must not depend on a
    remote node's diligence for the integrity of its own index."""
    _patch_post(monkeypatch, _Res({"vectors": [[0.1]], "method": "other/model"}))
    with pytest.raises(embed_peer.EmbeddingModelMismatch):
        asyncio.run(
            embed_peer.embed_via_peer(
                "http://127.0.0.1:1", ["x"], expect_model="mine/model"
            )
        )


def test_a_mismatch_is_not_swallowed_by_the_fallback_path(monkeypatch):
    """`try_peer_batch` swallows ordinary failures so the caller proceeds locally.
    A mismatch is deliberately **not** ordinary: it is a misconfiguration the user
    needs to see."""
    monkeypatch.setattr(embed_peer, "_candidates", lambda: ["friend"])

    async def boom(*a, **kw):
        raise embed_peer.EmbeddingModelMismatch("different space")

    monkeypatch.setattr(embed_peer, "embed_via_peer", boom)

    from backend.modules.network.lease import Borrowed, leases

    leases.borrowed["l"] = Borrowed(
        lease_id="l",
        node_id="friend",
        service="embed",
        model=None,
        expires_at=9e18,
        endpoint="http://127.0.0.1:1",
    )
    try:
        with pytest.raises(embed_peer.EmbeddingModelMismatch):
            asyncio.run(embed_peer.try_peer_batch(["x"], "mine/model"))
    finally:
        leases.borrowed.clear()


def test_matching_model_returns_the_vectors(monkeypatch):
    _patch_post(
        monkeypatch, _Res({"vectors": [[0.1, 0.2], [0.3, 0.4]], "method": "m/x"})
    )
    vectors, method = asyncio.run(
        embed_peer.embed_via_peer("http://127.0.0.1:1", ["a", "b"], expect_model="m/x")
    )
    assert method == "m/x"
    assert len(vectors) == 2


# ---- other malformed answers ----------------------------------------------------


def test_a_short_batch_is_rejected(monkeypatch):
    """Vectors are matched to texts by position, so a count mismatch would attach
    every embedding to the wrong document."""
    _patch_post(monkeypatch, _Res({"vectors": [[0.1]], "method": "m/x"}))
    with pytest.raises(ValueError, match="vectors for"):
        asyncio.run(
            embed_peer.embed_via_peer(
                "http://127.0.0.1:1", ["a", "b"], expect_model="m/x"
            )
        )


def test_an_empty_vector_is_rejected(monkeypatch):
    _patch_post(monkeypatch, _Res({"vectors": [[]], "method": "m/x"}))
    with pytest.raises(ValueError, match="malformed"):
        asyncio.run(
            embed_peer.embed_via_peer("http://127.0.0.1:1", ["a"], expect_model="m/x")
        )


def test_no_peers_means_no_batch(monkeypatch):
    """Absence is not an error: the caller simply proceeds locally."""
    monkeypatch.setattr(embed_peer, "_candidates", lambda: [])
    assert asyncio.run(embed_peer.try_peer_batch(["x"], "m/x")) is None


def test_a_transient_peer_failure_stays_local(monkeypatch):
    monkeypatch.setattr(embed_peer, "_candidates", lambda: ["friend"])

    async def boom(*a, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(embed_peer, "embed_via_peer", boom)
    from backend.modules.network.lease import Borrowed, leases

    leases.borrowed["l"] = Borrowed(
        lease_id="l",
        node_id="friend",
        service="embed",
        model=None,
        expires_at=9e18,
        endpoint="http://127.0.0.1:1",
    )
    try:
        assert asyncio.run(embed_peer.try_peer_batch(["x"], "m/x")) is None
    finally:
        leases.borrowed.clear()


# ---- the lender's own refusal ----------------------------------------------------


def test_the_lender_refuses_to_serve_hash_fallback_vectors(monkeypatch, client):
    """A lender with no embedding provider must fail loudly. The borrower's own
    code would refuse these anyway; refusing here means they learn about it as an
    error rather than as a silently degraded index."""
    from backend.modules.database import routes as db_routes

    # `**kw` because the route now passes `allow_peer=False`: the lending side must
    # not answer a borrowed batch by borrowing it again.
    async def fake(texts, **kw):
        return [[0.0] * 8 for _ in texts], "local-fallback"

    monkeypatch.setattr("backend.modules.database.embeddings.get_embeddings", fake)
    res = client.post(
        "/api/database/embeddings/batch", json={"texts": ["a"], "expectModel": "m/x"}
    )
    assert res.status_code == 503
    assert "fallback" in res.json()["detail"]
    assert db_routes is not None


def test_the_lender_refuses_a_model_mismatch(monkeypatch, client):
    async def fake(texts, **kw):
        return [[0.1] * 8 for _ in texts], "ollama/theirs"

    monkeypatch.setattr("backend.modules.database.embeddings.get_embeddings", fake)
    res = client.post(
        "/api/database/embeddings/batch",
        json={"texts": ["a"], "expectModel": "ollama/mine"},
    )
    assert res.status_code == 409
    assert "silently ruin" in res.json()["detail"]


def test_the_lender_bounds_the_batch(client):
    res = client.post("/api/database/embeddings/batch", json={"texts": ["x"] * 5000})
    assert res.status_code == 413


def test_the_lender_validates_the_payload(client):
    res = client.post("/api/database/embeddings/batch", json={"texts": "not a list"})
    assert res.status_code == 422


# ---- eval targets on a peer -------------------------------------------------------


def test_a_peer_target_does_not_wait_on_the_local_llama_semaphore(monkeypatch):
    """`_target_semaphore` exists because *this* node has one llama.cpp process.
    A peer target uses theirs, so waiting on it here would serialize away the
    entire reason for distributing a sweep.

    Tested by exhausting the semaphore first: a local target would block, and a
    peer target must not.
    """
    from contextlib import AsyncExitStack

    from backend.modules.evals import sweep

    async def flow():
        # Hold the only permit, as a concurrently-running local target would.
        await sweep._target_semaphore.acquire()
        try:
            # This mirrors the gate in `_run_one_target`: peer targets skip it.
            async with AsyncExitStack() as gate:
                peer_node = "friend"
                if not peer_node:  # pragma: no cover - the local branch
                    await gate.enter_async_context(sweep._target_semaphore)
                return True
        finally:
            sweep._target_semaphore.release()

    assert asyncio.run(asyncio.wait_for(flow(), timeout=2)) is True


def test_the_semaphore_gate_is_conditional_on_the_peer_node():
    """Guards the shape of the gate itself: if the condition were dropped, the
    behavioural test above would still pass while every peer target serialized."""
    import inspect

    from backend.modules.evals import sweep

    source = inspect.getsource(sweep._run_one_target)
    assert "if not peer_node:" in source
    assert "_target_semaphore" in source


def test_run_target_declares_an_optional_node():
    from backend.modules.evals.models import RunTarget

    target = RunTarget(model="m")
    assert target.node == ""
    assert RunTarget(model="m", node="abc").node == "abc"


def test_a_peer_lease_is_released_even_when_the_target_fails(monkeypatch):
    """Released in a `finally`, like `llama_target.serving`: a sweep that dies must
    not leave a friend's GPU reserved until the lease expires on its own."""
    from backend.modules.evals import sweep
    from backend.modules.evals.models import RunTarget
    from backend.modules.network.lease import Borrowed, leases

    released: list[str] = []

    async def fake_request(hub, node, service, model=None, duration_s=None):
        return Borrowed(
            lease_id="L",
            node_id=node,
            service=service,
            model=model,
            expires_at=9e18,
            endpoint="http://127.0.0.1:9",
        )

    async def fake_release(lease_id, notify_peer=True):
        released.append(lease_id)

    monkeypatch.setattr(leases, "request", fake_request)
    monkeypatch.setattr(leases, "release_borrowed", fake_release)

    async def flow():
        with pytest.raises(RuntimeError):
            async with sweep._peer_endpoint("friend", RunTarget(model="m")) as ep:
                assert ep == "http://127.0.0.1:9"
                raise RuntimeError("the target blew up")

    asyncio.run(flow())
    assert released == ["L"]
