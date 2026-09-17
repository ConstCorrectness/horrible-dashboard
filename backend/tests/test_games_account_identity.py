"""The account is the identity: every machine signed in to one account is one person.

Machines used to mint their own person keys and bind them to the account they
signed in to, so two computers on one account took turns owning its username —
whichever had started last. The game server now holds one key per account and signs
the certificate of every machine that enrolls.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend.games_server import store
from backend.modules.network import identity as node_identity
from backend.modules.social import handles
from backend.modules.social import identity as person_identity


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GAMES_ALLOW_DEV_AUTH", "1")  # bearer == account id
    person_identity._cached_identity.cache_clear()
    node_identity._cached_identity.cache_clear()
    from backend.games_server.app import app

    store.init_db()
    for account_id, handle in (("acc1", "rob"), ("acc2", "ann")):
        _account(account_id, handle)
    yield TestClient(app)
    person_identity._cached_identity.cache_clear()
    node_identity._cached_identity.cache_clear()


def _account(account_id: str, handle: str | None) -> None:
    with store.get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (id, provider, subject, display_name, created_at, handle)"
            " VALUES (?, 'test', ?, ?, ?, ?)",
            (account_id, account_id, account_id, time.time(), handle),
        )


def _machine() -> node_identity.Identity:
    return node_identity.Identity(Ed25519PrivateKey.generate())


def _enroll(client, account_id, node, *, ts=None, sign_as=None, bearer=None):
    ts = time.time() if ts is None else ts
    signer = sign_as or node
    return client.post(
        "/account/devices",
        headers={"Authorization": f"Bearer {bearer or account_id}"},
        json={
            "node_id": node.node_id,
            "node_public_key": node.public_key,
            "ts": ts,
            "sig": signer.sign(handles.enroll_challenge(account_id, node.node_id, ts)),
        },
    ).json()


def test_two_machines_on_one_account_are_one_person(client) -> None:
    """The regression: the second sign-in used to take the username from the first."""
    desktop, laptop = _machine(), _machine()
    first = _enroll(client, "acc1", desktop)
    second = _enroll(client, "acc1", laptop)

    assert first["ok"] and second["ok"]
    assert first["cert"]["person_id"] == second["cert"]["person_id"]
    # The second learns about the first, so the two can find each other.
    assert [d["node_id"] for d in second["devices"]] == [desktop.node_id]

    entry = client.get("/directory/resolve", params={"handle": "rob"}).json()["entry"]
    assert entry["person_id"] == first["cert"]["person_id"]
    assert {d["node_id"] for d in entry["devices"]} == {desktop.node_id, laptop.node_id}
    # And a friend's node believes every one of them.
    assert len(handles.verified_devices(entry)) == 2


def test_the_certificate_verifies_on_the_node_and_carries_no_hostname(client) -> None:
    cert = _enroll(client, "acc1", _machine())["cert"]
    assert person_identity.verify_device_cert(cert)
    assert cert["label"] == ""


def test_the_node_and_server_sign_the_same_challenge() -> None:
    """Written in two files that cannot import each other; pinned together here."""
    assert handles.enroll_challenge("a", "n", 1.5) == store.enroll_challenge(
        "a", "n", 1.5
    )


def test_accounts_are_different_people(client) -> None:
    a = _enroll(client, "acc1", _machine())["cert"]
    b = _enroll(client, "acc2", _machine())["cert"]
    assert a["person_id"] != b["person_id"]


def test_a_machine_must_hold_the_key_it_names(client) -> None:
    """Otherwise a signed-in user could list someone else's machine under their own
    username, and everyone adding them would dial a stranger."""
    victim, attacker = _machine(), _machine()
    reply = _enroll(client, "acc1", victim, sign_as=attacker)
    assert "error" in reply
    assert store.devices_for_person(store.account_identity("acc1")[0]) == []


def test_a_proof_for_one_account_does_not_enroll_in_another(client) -> None:
    reply = _enroll(client, "acc1", _machine(), bearer="acc2")
    assert "error" in reply


def test_an_old_proof_is_refused(client) -> None:
    reply = _enroll(client, "acc1", _machine(), ts=time.time() - 3600)
    assert "clock" in reply["error"]


def test_enrolling_needs_a_sign_in(client) -> None:
    node = _machine()
    reply = client.post(
        "/account/devices",
        json={
            "node_id": node.node_id,
            "node_public_key": node.public_key,
            "ts": 0,
            "sig": "",
        },
    ).json()
    assert reply == {"error": "sign in required"}


def test_the_private_key_never_leaves_its_table(client) -> None:
    _, _, private = store.account_identity("acc1")
    _enroll(client, "acc1", _machine())
    assert private not in json.dumps(store.get_account("acc1"))
    assert private not in json.dumps(store.account_by_handle("rob"))


def test_the_identity_is_stable(client) -> None:
    assert store.account_identity("acc1") == store.account_identity("acc1")
    assert store.account_identity("nobody") is None


def test_signing_out_unlists_the_machine(client) -> None:
    node = _machine()
    person = _enroll(client, "acc1", node)["cert"]["person_id"]
    client.delete(
        f"/account/devices/{node.node_id}", headers={"Authorization": "Bearer acc1"}
    )
    assert store.devices_for_person(person) == []


def test_machines_are_capped_newest_first(client, monkeypatch) -> None:
    monkeypatch.setattr(store, "MAX_DEVICES_PER_PERSON", 2)
    nodes = [_machine() for _ in range(3)]
    for node in nodes:
        _enroll(client, "acc1", node)
    person = store.account_identity("acc1")[0]
    listed = [d["node_id"] for d in store.devices_for_person(person)]
    assert listed == [nodes[2].node_id, nodes[1].node_id]


def test_the_node_adopts_its_accounts_identity_and_drops_it_on_sign_out(
    client, tmp_path, monkeypatch
) -> None:
    """End to end through the node's client: enroll, adopt, sign out, forget."""
    from backend.games_server.app import app
    from backend.modules.games import server_auth
    from backend.modules.social import store as social_store

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        handles.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.ASGITransport(app=app), **kw),
    )
    social_store.init_social_db()
    sibling = _machine()
    _enroll(client, "acc1", sibling)
    (tmp_path / "games_token.json").write_text(
        json.dumps({"token": "acc1", "account": {"id": "acc1", "handle": "rob"}})
    )
    monkeypatch.setattr(server_auth, "_is_expired", lambda _token: False)

    result = asyncio.run(handles.enroll_device())

    assert result.get("ok"), result
    account_person = store.account_identity("acc1")[0]
    assert person_identity.effective_person_id() == account_person
    # Its sibling is recorded as ours, reachable over the relay.
    siblings = {d["node_id"]: d for d in social_store.list_devices(account_person)}
    assert siblings[sibling.node_id]["last_address"] == f"relay:{sibling.node_id}"

    asyncio.run(handles.unenroll_device())
    server_auth.sign_out()
    assert not person_identity.is_linked_device()
    listed = [d["node_id"] for d in store.devices_for_person(account_person)]
    assert listed == [sibling.node_id]
