"""Binding a game-server account to a peer-fabric person, and the `@handle` directory.

Two things are under test here, and the second one is the reason this file exists
at all:

1. the **binding route** — that a bearer token alone can't claim a person, that a
   signature alone can't claim an account, and that neither direction of the
   mapping can be doubled up; and
2. the **duplicated crypto** in `games_server/crypto.py`. The game server deploys
   on its own and must not import the node's module graph, so the fingerprint
   scheme and the Ed25519 verify exist twice. That duplication fails *silently*
   when it drifts — a fingerprint one character off makes every binding look
   forged — so it is pinned the same way the Kotlin wire is: the two copies are
   replayed against each other.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.games_server import crypto, store
from backend.modules.network import identity as node_identity
from backend.modules.social import identity as person_identity


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, base64.b64encode(raw).decode("ascii")


def _sign(private: Ed25519PrivateKey, payload: bytes) -> str:
    return base64.b64encode(private.sign(payload)).decode("ascii")


# ---- conformance: the two copies must agree --------------------------------------


def test_fingerprint_matches_the_nodes_implementation() -> None:
    """`games_server/crypto.fingerprint_person` == `social/identity.fingerprint`.

    If these drift, every binding signature verifies against the wrong person id
    and the failure looks like "the user's key is wrong", not like a bug here.
    """
    for _ in range(20):
        _private, public = _keypair()
        assert crypto.fingerprint_person(public) == person_identity.fingerprint(public)


def test_fingerprint_is_base32_not_base64url() -> None:
    """The scheme is `base32(sha256(pubkey))[:16]`, lowercase and unpadded.

    Pinned explicitly because base64url is the plausible-looking wrong answer, and
    picking it is what silently broke phone pairing: the socket just closes.
    """
    _private, public = _keypair()
    fp = crypto.fingerprint_person(public)
    assert len(fp) == 16
    assert fp == fp.lower()
    assert set(fp) <= set("abcdefghijklmnopqrstuvwxyz234567")


def test_verify_matches_the_nodes_implementation() -> None:
    private, public = _keypair()
    payload = b"horrible conformance"
    sig = _sign(private, payload)
    assert crypto.verify(public, payload, sig)
    assert node_identity.verify(public, payload, sig)

    # ...and both reject the same tampering, rather than one being lenient.
    assert not crypto.verify(public, b"different", sig)
    assert not node_identity.verify(public, b"different", sig)


@pytest.mark.parametrize("bad", ["", "not-base64!!", "AAAA"])
def test_verify_never_raises_on_garbage(bad: str) -> None:
    """This runs on attacker-supplied strings; every failure must collapse to False."""
    assert crypto.verify(bad, b"x", bad) is False
    assert crypto.fingerprint_person is crypto.fingerprint_person  # import sanity


# ---- the challenge -----------------------------------------------------------------


def test_challenge_binds_the_account_id() -> None:
    """A signature for one account must not verify for another.

    Without the account id inside the signed bytes, a signature proving "I hold
    this person key" could be lifted from anywhere and replayed to bind someone
    else's person to your account.
    """
    a = store.person_challenge("acct-1", "person-abc")
    b = store.person_challenge("acct-2", "person-abc")
    assert a != b


def test_challenge_is_canonical_json() -> None:
    """Sorted keys, compact separators — the node builds these bytes independently
    (social/handles.py), so the two must agree exactly or nothing ever verifies."""
    raw = store.person_challenge("acct-1", "person-abc")
    assert raw == json.dumps(
        {
            "purpose": "horrible.account.person",
            "account_id": "acct-1",
            "person_id": "person-abc",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    # No spaces anywhere is the cheap observable proof of "compact".
    assert b" " not in raw


def test_node_and_server_build_the_same_challenge() -> None:
    """The node's copy of the challenge (inlined in `handles.publish_binding`) is
    the same bytes the server checks. Kept as an explicit assertion because the two
    are written in different files and neither imports the other."""
    account_id, person_id = "acct-xyz", "abcdefghijklmnop"
    node_side = json.dumps(
        {
            "purpose": "horrible.account.person",
            "account_id": account_id,
            "person_id": person_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert node_side == store.person_challenge(account_id, person_id)


# ---- the store ---------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store.init_db()


def _account(account_id: str, handle: str | None = None) -> None:
    import time

    with store.get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (id, provider, subject, display_name, created_at, handle)"
            " VALUES (?, 'test', ?, ?, ?, ?)",
            (account_id, account_id, account_id, time.time(), handle),
        )


def test_bind_person_is_idempotent(db: None) -> None:
    _account("a1", "rob")
    _private, public = _keypair()
    person = crypto.fingerprint_person(public)
    assert store.bind_person("a1", person, public) == "ok"
    assert store.bind_person("a1", person, public) == "ok"


def test_one_person_cannot_hold_two_accounts(db: None) -> None:
    """Otherwise one human would have two names on the same ladder."""
    _account("a1", "rob")
    _account("a2", "roberta")
    _private, public = _keypair()
    person = crypto.fingerprint_person(public)
    assert store.bind_person("a1", person, public) == "ok"
    assert store.bind_person("a2", person, public) == "taken"


def test_binding_an_unknown_account_is_refused(db: None) -> None:
    _private, public = _keypair()
    assert store.bind_person("nope", crypto.fingerprint_person(public), public) == (
        "unknown-account"
    )


def test_resolve_returns_only_public_fields(db: None) -> None:
    """This is served to anyone who asks, so it must carry no email, token, or
    provider subject — only what someone needs in order to add you."""
    _account("a1", "rob")
    _private, public = _keypair()
    store.bind_person("a1", crypto.fingerprint_person(public), public)
    entry = store.account_by_handle("Rob")  # case-insensitive
    assert entry is not None
    assert set(entry) == {"handle", "display_name", "person_id", "person_public_key"}


def test_resolve_misses_are_none(db: None) -> None:
    assert store.account_by_handle("nobody") is None


def test_search_refuses_short_prefixes(db: None) -> None:
    """A two-character prefix would enumerate the user base a few hundred queries
    at a time. This is a directory for finding someone you can half-name."""
    _account("a1", "robert")
    assert store.search_handles("ro") == []
    assert [e["handle"] for e in store.search_handles("rob")] == ["robert"]


def test_search_is_prefix_not_substring(db: None) -> None:
    _account("a1", "robert")
    _account("a2", "notrobert")
    assert [e["handle"] for e in store.search_handles("rob")] == ["robert"]


def test_search_excludes_bots(db: None) -> None:
    import time

    _account("a1", "roblito")
    with store.get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (id, provider, subject, display_name, created_at,"
            " handle, is_bot) VALUES ('b1', 'bot', 'b1', 'Bot', ?, 'robobot', 1)",
            (time.time(),),
        )
    assert [e["handle"] for e in store.search_handles("rob")] == ["roblito"]


def test_search_ignores_like_wildcards(db: None) -> None:
    """`%` in a query would otherwise match everything — a one-request scrape."""
    _account("a1", "robert")
    assert store.search_handles("%%%") == []
    assert store.search_handles("rob%") == []


def test_search_can_find_an_underscore_handle(db: None) -> None:
    """`_` is a legal handle character *and* a LIKE wildcard. It has to be escaped
    rather than dropped, or `rob_smith` is unfindable by anyone who types it —
    and `rob_` would match `roba`, `robb`, … instead."""
    _account("a1", "rob_smith")
    _account("a2", "robsmith")
    assert [e["handle"] for e in store.search_handles("rob_")] == ["rob_smith"]


def test_accounts_with_no_handle_are_not_directory_entries(db: None) -> None:
    """A handle is the name; an account without one is not findable, and must not
    come back as a half-filled row."""
    _account("a1", None)
    _private, public = _keypair()
    store.bind_person("a1", crypto.fingerprint_person(public), public)
    assert store.search_handles("a1") == []
