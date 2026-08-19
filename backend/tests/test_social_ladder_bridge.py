"""The bridge between the fabric roster and the ladder (`social/ladder.py`).

Two friend systems used to describe the same human with no way to tell — the
fabric roster keyed by `person_id`, the ladder keyed by `account_id`. This is the
join, and the thing it must not do is as important as the thing it must:

- the **directory lookup** may learn a person's username, and
- it must **refuse an entry whose person id is not the fingerprint of the key it
  arrived with** — otherwise a hostile game server could point a person you already
  trust at a key that isn't theirs, and the roster would render the lie as fact.

The second is why `resolve_people` re-checks every entry rather than trusting the
server's arithmetic, exactly as `handles.resolve` does for one username.
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.games_server import crypto
from backend.games_server import store as gstore
from backend.modules.social import ladder
from backend.modules.social import store as sstore


def _public_key() -> str:
    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    return base64.b64encode(raw).decode("ascii")


# ---- game server: person -> account ------------------------------------------------


@pytest.fixture
def gdb(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    gstore.init_db()


def _account(account_id: str, handle: str | None = None) -> str:
    import time

    with gstore.get_conn() as conn:
        conn.execute(
            "INSERT INTO accounts (id, provider, subject, display_name, created_at, handle)"
            " VALUES (?, 'test', ?, ?, ?, ?)",
            (account_id, account_id, account_id, time.time(), handle),
        )
    public = _public_key()
    gstore.bind_person(account_id, crypto.fingerprint_person(public), public)
    return crypto.fingerprint_person(public)


def test_accounts_by_person_finds_bound_accounts(gdb: None) -> None:
    person = _account("a1", "rob")
    found = gstore.accounts_by_person([person])
    assert found[person]["handle"] == "rob"
    assert found[person]["account_id"] == "a1"


def test_accounts_by_person_omits_unbound_people(gdb: None) -> None:
    """A person with no ladder account is a normal roster row, not an error."""
    _account("a1", "rob")
    assert gstore.accounts_by_person(["nosuchperson0000"]) == {}


def test_accounts_by_person_omits_accounts_with_no_handle(gdb: None) -> None:
    """`_directory_row` returns None without a handle, and a None must not become
    an entry — the reconciler would cache a username of `None` as if it were one."""
    person = _account("a1", handle=None)
    assert gstore.accounts_by_person([person]) == {}


def test_accounts_by_person_is_capped(gdb: None) -> None:
    """A directory lookup must not be a way to enumerate the whole user base."""
    person = _account("a1", "rob")
    padded = ["x" * 16] * (gstore.MAX_PERSON_LOOKUP + 50) + [person]
    # The real person is past the cap, so it is dropped rather than answered.
    assert gstore.accounts_by_person(padded) == {}


def test_accounts_by_person_ignores_blanks(gdb: None) -> None:
    person = _account("a1", "rob")
    assert set(gstore.accounts_by_person(["", "  ", person])) == {person}


# ---- node: the fingerprint invariant -----------------------------------------------


@pytest.fixture
def node_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    sstore.init_social_db()


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stands in for httpx.AsyncClient, returning one canned directory answer."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse(self._payload)


def _patch_directory(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(ladder, "_base", lambda: "http://gameserver.test")
    monkeypatch.setattr(ladder.httpx, "AsyncClient", lambda **_kw: _FakeClient(payload))


def test_resolve_people_accepts_a_consistent_entry(monkeypatch) -> None:
    public = _public_key()
    person = crypto.fingerprint_person(public)
    _patch_directory(
        monkeypatch,
        {
            "people": {
                person: {
                    "handle": "rob",
                    "display_name": "Rob",
                    "person_id": person,
                    "person_public_key": public,
                    "account_id": "a1",
                }
            }
        },
    )
    found = asyncio.run(ladder.resolve_people([person]))
    assert found[person]["handle"] == "rob"


def test_resolve_people_rejects_a_mismatched_fingerprint(monkeypatch) -> None:
    """The security boundary: a directory that names a person but hands over a key
    that isn't theirs is answering for someone else. Dropping the entry means a
    hostile server can withhold a binding, never forge one."""
    theirs = _public_key()
    attacker = _public_key()
    person = crypto.fingerprint_person(theirs)
    _patch_directory(
        monkeypatch,
        {
            "people": {
                person: {
                    "handle": "rob",
                    "person_id": person,
                    "person_public_key": attacker,  # not the key `person` came from
                    "account_id": "a1",
                }
            }
        },
    )
    assert asyncio.run(ladder.resolve_people([person])) == {}


def test_resolve_people_survives_a_malformed_answer(monkeypatch) -> None:
    """Reconciliation is advisory — a broken directory degrades to "no usernames",
    never to a failed roster."""
    for payload in ({}, {"people": None}, {"people": {"p": "not-a-dict"}}):
        _patch_directory(monkeypatch, payload)
        assert asyncio.run(ladder.resolve_people(["p"])) == {}


def test_resolve_people_short_circuits_on_no_ids(monkeypatch) -> None:
    """The steady state after everything is linked: no request at all."""

    def _boom(**_kw: object) -> object:
        raise AssertionError("should not have called the directory")

    monkeypatch.setattr(ladder.httpx, "AsyncClient", _boom)
    assert asyncio.run(ladder.resolve_people([])) == {}


# ---- node: the roster cache --------------------------------------------------------


def test_ladder_identity_round_trips(node_db: None) -> None:
    sstore.upsert_friend("person00000000aa", display_name="Rob", status="accepted")
    sstore.set_ladder_identity("person00000000aa", handle="rob", account_id="a1")
    row = sstore.get_friend_row("person00000000aa")
    assert row is not None
    assert (row["handle"], row["account_id"]) == ("rob", "a1")
    assert sstore.person_for_account("a1") == "person00000000aa"


def test_ladder_identity_does_not_create_rows(node_db: None) -> None:
    """Learning a stranger's username is not a reason to put them in your roster."""
    sstore.set_ladder_identity("stranger00000000", handle="nope", account_id="a9")
    assert sstore.get_friend_row("stranger00000000") is None


def test_missing_ladder_identity_is_the_worklist(node_db: None) -> None:
    sstore.upsert_friend("person00000000aa", display_name="Rob", status="accepted")
    sstore.upsert_friend("person00000000bb", display_name="Ann", status="accepted")
    sstore.set_ladder_identity("person00000000aa", handle="rob", account_id="a1")
    pending = [r["person_id"] for r in sstore.friends_missing_ladder_identity()]
    assert pending == ["person00000000bb"]


def test_build_friend_tolerates_rows_without_the_new_columns(node_db: None) -> None:
    """The columns arrive by ALTER, so a hand-built row (a fixture, an older code
    path) legitimately lacks them and must not raise."""
    friend = sstore.build_friend(
        {
            "person_id": "person00000000aa",
            "display_name": "Rob",
            "person_public_key": "",
            "status": "accepted",
            "note": None,
            "added_at": 0.0,
            "is_self": 0,
        },
        set(),
    )
    assert friend.handle is None and friend.account_id is None
