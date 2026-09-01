"""What the browser reads: the bench/lease routes, and the two "where did this run"
fields the panes render.

The theme is the same one that runs through this whole slice: **a peer's work must
never look local.** A borrowed lease reaches its lender through a `127.0.0.1`
tunnel port, so every honest signal here has to be recorded explicitly — an eval
run's `node`, an embedding batch's `method`. Anything derived from the endpoint
would say "local" for both.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    # Keyed by path, but the module-level set survives the env change, so clear it
    # or the eval tables land in the previous test's database.
    from backend.modules.evals import store

    store._initialized.clear()
    return TestClient(app)


# ---- the bench route -------------------------------------------------------------


def test_local_mode_needs_no_peer(client):
    res = client.post("/api/network/bench", json={"mode": "local"})
    assert res.status_code == 200
    body = res.json()["results"][0]
    assert body["mode"] == "local"
    assert body["phases"]


def test_percentiles_are_reported_never_a_mean(client):
    """A mean over a bimodal blocked-pump distribution hides exactly the bug the
    bench exists to find."""
    phase = client.post("/api/network/bench", json={"mode": "local"}).json()["results"][
        0
    ]["phases"][0]
    assert {"p50Ms", "p90Ms", "p99Ms", "maxMs"} <= set(phase)
    assert not any("mean" in k.lower() for k in phase)


def test_measuring_an_unconnected_peer_404s(client):
    res = client.post("/api/network/bench", json={"mode": "echo", "node_id": "nobody"})
    assert res.status_code == 404


def test_a_second_concurrent_bench_is_refused(client, monkeypatch):
    """Two runs would each measure the other's traffic and report it as this
    link's latency — the one failure mode a measurement tool must not have."""
    from backend.modules.network import routes

    async def flow():
        async with routes._bench_lock:
            assert routes._bench_lock.locked()

    asyncio.run(flow())
    monkeypatch.setattr(routes._bench_lock, "locked", lambda: True)
    assert client.post("/api/network/bench", json={"mode": "local"}).status_code == 409


# ---- the lease routes ------------------------------------------------------------


def test_leases_report_the_lending_stance_alongside_the_list(client):
    """An empty `granted` means something different on a node with lending off
    than on one that is simply idle."""
    body = client.get("/api/network/leases").json()
    assert body["granted"] == [] and body["borrowed"] == []
    assert body["lending"]["enabled"] is False
    assert body["lending"]["policy"] in ("ask", "trusted", "off")


def test_ending_an_unknown_lease_404s(client):
    assert client.delete("/api/network/leases/nope").status_code == 404


def test_one_route_ends_a_lease_in_either_direction(client, monkeypatch):
    from backend.modules.network.hub import peer_hub
    from backend.modules.network.lease import leases

    async def fake_revoke(hub, lease_id, reason="revoked"):
        leases.granted.pop(lease_id, None)
        return True

    monkeypatch.setattr(leases, "revoke", fake_revoke)
    lease = leases.grant("borrower", "llama", None, 60.0)
    try:
        res = client.delete(f"/api/network/leases/{lease.lease_id}")
        assert res.json() == {"ok": True, "released": "granted", "node": "borrower"}
        assert asyncio.run(leases.end(peer_hub, lease.lease_id))["ok"] is False
    finally:
        leases.granted.clear()


def test_a_lease_change_is_pushed_not_polled(monkeypatch):
    """The transitions that matter originate on the *other* node — a peer revoking
    mid-turn, a lease expiring — so a view refreshing only on its own actions
    would keep showing a lease already taken away."""
    from backend.modules.network import lease as lease_mod

    seen: list[tuple[str, dict]] = []

    class FakeHub:
        def register_handler(self, *a, **kw):
            pass

        def emit(self, event, data):
            seen.append((event, data))

    monkeypatch.setattr(lease_mod.leases, "start", lambda hub: None)
    monkeypatch.setattr(lease_mod.tunnels, "set_authorizer", lambda fn: None)
    monkeypatch.setattr(lease_mod.tunnels, "register_service", lambda *a: None)
    monkeypatch.setattr(lease_mod.leases, "_listeners", [])
    lease_mod.register(FakeHub())
    try:
        lease_mod.leases.grant("borrower", "llama", None, 60.0)
    finally:
        lease_mod.leases.granted.clear()
        lease_mod.leases._listeners.clear()
    assert [e for e, _ in seen] == ["lease_update"]


# ---- "ran on", recorded rather than inferred --------------------------------------


def test_an_eval_run_records_the_node_it_ran_on(client):
    """Not derived from the endpoint: a peer target reaches its lender through a
    local tunnel port, so the endpoint reads `127.0.0.1` either way."""
    from backend.modules.evals import store

    store.init_evals_db()
    local = store.create_run("s", "m", "llamacpp", "http://127.0.0.1:8080", "m", 1)
    remote = store.create_run(
        "s", "m", "llamacpp", "http://127.0.0.1:51234", "m", 1, node="friend"
    )
    assert store.get_run(local.id).node == ""
    assert store.get_run(remote.id).node == "friend"


def test_the_node_survives_the_response_model(client):
    """A Pydantic response model silently filters a field the DB now carries, so
    the assertion has to be on the HTTP response rather than on the row."""
    from backend.modules.evals import store

    store.init_evals_db()
    run = store.create_run("s", "m", "p", "e", "m", 1, node="friend")
    rows = client.get("/api/evals/runs").json()["runs"]
    assert any(r["id"] == run.id and r["node"] == "friend" for r in rows)


# ---- the embedding method names the node -------------------------------------------


def test_a_peer_batch_says_which_node_produced_it(monkeypatch):
    """`embed_via_peer` knows only a tunnel endpoint, and a tunnel endpoint is
    `127.0.0.1` — a result carrying it would read as local."""
    from backend.modules.database import embed_peer
    from backend.modules.network.lease import Borrowed, leases

    async def fake(endpoint, texts, *, expect_model):
        return [[0.1]], expect_model

    monkeypatch.setattr(embed_peer, "embed_via_peer", fake)
    leases.borrowed["L"] = Borrowed(
        lease_id="L",
        node_id="friend",
        service="embed",
        model=None,
        expires_at=9e18,
        endpoint="http://127.0.0.1:51234",
    )
    try:
        _, method = asyncio.run(embed_peer.try_peer_batch(["x"], "ollama/all-minilm"))
    finally:
        leases.borrowed.clear()
    assert method == "peer:friend/ollama/all-minilm"


def test_offload_is_off_by_default(client):
    """Shipping every index build to a friend's machine should be a choice."""
    from backend.modules.database.embeddings import _peer_offload_enabled

    assert _peer_offload_enabled() is False


def test_the_lender_does_not_re_borrow_the_batch_it_is_serving(monkeypatch, client):
    """Without `allow_peer=False` a lender whose own offload setting is on would
    answer a borrowed batch by borrowing it again, and a two-node pair would hand
    one batch back and forth."""
    import inspect

    from backend.modules.database import routes as db_routes

    assert "allow_peer=False" in inspect.getsource(db_routes.embed_batch)


def test_the_offload_path_is_skipped_when_disabled(monkeypatch):
    """Guards the gate itself: without it, the setting would be decorative."""
    from backend.modules.database import embed_peer, embeddings

    called = []

    async def spy(texts, model):
        called.append(model)
        return None

    monkeypatch.setattr(embed_peer, "try_peer_batch", spy)
    monkeypatch.setattr(embeddings, "_peer_offload_enabled", lambda: False)

    async def no_model(client):
        return None

    monkeypatch.setattr(embeddings, "_resolve_model", no_model)
    asyncio.run(embeddings.get_embeddings(["x"]))
    assert called == []
