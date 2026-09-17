"""`@username` leads to machines, not just a person.

A username resolved to a person key, but which machines that person runs came only
from the Atlas presence directory, and an ordinary install has no Atlas credentials.
So a username resolved and then reached nobody. The game server now lists each bound
person's machines as device certificates signed by the person key.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.games_server import store
from backend.modules.network import identity as node_identity
from backend.modules.social import handles
from backend.modules.social import identity as person_identity
from backend.tests.test_games_person_binding import _account


@pytest.fixture()
def db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store.init_db()


def _person() -> person_identity.PersonIdentity:
    return person_identity.PersonIdentity(Ed25519PrivateKey.generate())


def _node() -> node_identity.Identity:
    return node_identity.Identity(Ed25519PrivateKey.generate())


def _bound(account_id: str, handle: str) -> person_identity.PersonIdentity:
    person = _person()
    _account(account_id, handle)
    assert store.bind_person(account_id, person.person_id, person.public_key) == "ok"
    return person


def test_the_certificate_bytes_match_the_nodes(db) -> None:
    """Duplicated so the game server needs no node module; pinned so it cannot drift."""
    person, node = _person(), _node()
    cert = person.issue_device_cert(node.node_id, node.public_key, "laptop")
    assert store.cert_bytes(cert) == person_identity.canonical_cert_bytes(cert)


def test_a_bound_persons_machine_is_listed_and_resolvable(db) -> None:
    person = _bound("a1", "ben")
    node = _node()
    cert = person.issue_device_cert(node.node_id, node.public_key, "")
    assert store.publish_device(cert) == "ok"

    entry = store.account_by_handle("ben")
    assert [d["node_id"] for d in entry["devices"]] == [node.node_id]
    # And the node side believes it: the certificate verifies under the entry.
    assert [c["node_id"] for c in handles.verified_devices(entry)] == [node.node_id]


def test_an_unbound_person_cannot_list_machines(db) -> None:
    person, node = _person(), _node()
    cert = person.issue_device_cert(node.node_id, node.public_key, "")
    assert store.publish_device(cert) == "unbound"


def test_a_self_consistent_key_is_not_enough_it_must_be_the_bound_one(db) -> None:
    """Otherwise anyone could list their own machines under a bound person's id by
    presenting a different key that happens to be internally consistent — it cannot
    be, since the id is the key's fingerprint, so this also pins that check."""
    _bound("a1", "ben")
    impostor, node = _person(), _node()
    cert = impostor.issue_device_cert(node.node_id, node.public_key, "")
    assert store.publish_device(cert) == "unbound"


@pytest.mark.parametrize("tamper", ["sig", "node_id", "person_id", "label"])
def test_a_tampered_certificate_is_refused(db, tamper) -> None:
    person = _bound("a1", "ben")
    node, other = _node(), _node()
    cert = person.issue_device_cert(node.node_id, node.public_key, "")
    if tamper == "sig":
        cert["sig"] = cert["sig"][:-4] + "AAA="
    elif tamper == "node_id":
        cert["node_id"] = other.node_id  # no longer the fingerprint of node_public_key
    elif tamper == "person_id":
        cert["person_id"] = _person().person_id
    else:
        cert["label"] = "renamed after signing"
    assert store.publish_device(cert) == "invalid"


def test_malformed_input_is_invalid_not_a_crash(db) -> None:
    for junk in (None, "cert", {}, {"person_id": 1}, {"person_public_key": "!!"}):
        assert store.publish_device(junk) == "invalid"


def test_machines_are_capped_newest_first(db, monkeypatch) -> None:
    monkeypatch.setattr(store, "MAX_DEVICES_PER_PERSON", 2)
    person = _bound("a1", "ben")
    nodes = [_node() for _ in range(3)]
    for node in nodes:
        cert = person.issue_device_cert(node.node_id, node.public_key, "")
        assert store.publish_device(cert) == "ok"
    listed = [d["node_id"] for d in store.devices_for_person(person.person_id)]
    assert listed == [nodes[2].node_id, nodes[1].node_id]


def test_the_node_ignores_a_directory_that_lies(db) -> None:
    """A hostile directory can list someone else's certificate under @ben. The node
    checks each one against the entry's own person key."""
    ben = _bound("a1", "ben")
    stranger, node = _person(), _node()
    entry = store.account_by_handle("ben")
    entry["devices"] = [
        {
            "node_id": node.node_id,
            "cert": stranger.issue_device_cert(node.node_id, node.public_key, ""),
        }
    ]
    assert handles.verified_devices(entry) == []
    assert ben.person_id == entry["person_id"]


def test_the_directory_certificate_carries_no_hostname(tmp_path, monkeypatch) -> None:
    """Anyone can resolve a username, so the published certificate is minted
    label-less rather than leaking the machine's name."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    person_identity._cached_identity.cache_clear()
    node_identity._cached_identity.cache_clear()
    try:
        cert = handles.directory_cert()
        assert cert["label"] == ""
        assert person_identity.verify_device_cert(cert)
        assert cert["node_id"] == node_identity.load_identity().node_id
    finally:
        person_identity._cached_identity.cache_clear()
        node_identity._cached_identity.cache_clear()
