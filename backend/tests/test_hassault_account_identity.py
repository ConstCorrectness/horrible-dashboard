"""Which account a node's hassault rows are filed under.

The bug this file exists for: three call sites read `signed_in_account()["account_id"]`,
a key that dict never has (it is `id`), so every match, skin and rating recorded
while signed in went to the signed-out stand-in. The tests that should have caught
it mocked the same wrong key, so they agreed with it.
"""

from __future__ import annotations

import pytest

from backend.modules.games import server_auth
from backend.modules.hassault import results
from backend.modules.hassault.skins import skin_manager


def _sign_in(monkeypatch, account_id: str | None) -> None:
    monkeypatch.setattr(
        server_auth,
        "signed_in_account",
        lambda: (
            None
            if account_id is None
            else {"id": account_id, "display_name": account_id, "handle": account_id}
        ),
    )


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    results._adoption_settled.clear()
    skin_manager._inventories.clear()
    yield
    results._adoption_settled.clear()
    skin_manager._inventories.clear()


def a_match() -> dict:
    return {
        "map": "hd_pit",
        "room": "r1",
        "name": "@rob",
        "kills": 3,
        "deaths": 1,
        "headKills": 1,
        "damageDealt": 400,
        "won": True,
        "mvp": False,
        "opponents": 2,
        "playedAt": 1000.0,
    }


def test_the_signed_in_id_is_read_from_the_real_shape(monkeypatch):
    _sign_in(monkeypatch, "acct-ada")
    assert server_auth.signed_in_account_id() == "acct-ada"
    assert results.current_account_id() == "acct-ada"


def test_signed_out_is_the_local_stand_in(monkeypatch):
    _sign_in(monkeypatch, None)
    assert server_auth.signed_in_account_id() is None
    assert results.current_account_id() == results.LOCAL_ACCOUNT


def test_the_first_account_adopts_the_stand_ins_rows(monkeypatch):
    _sign_in(monkeypatch, None)
    results.record(results.LOCAL_ACCOUNT, a_match())
    drop = skin_manager.roll_drop(results.LOCAL_ACCOUNT)

    _sign_in(monkeypatch, "acct-ada")
    assert results.current_account_id() == "acct-ada"

    assert results.latest("acct-ada") is not None
    assert results.latest(results.LOCAL_ACCOUNT) is None
    owned = {s.instance_id for s in skin_manager.get_inventory("acct-ada")}
    assert drop.instance_id in owned


def test_adoption_happens_once_per_node(monkeypatch):
    """A second account signing in later does not take the first one's rows, nor
    rows the stand-in recorded after the first adoption."""
    _sign_in(monkeypatch, None)
    results.record(results.LOCAL_ACCOUNT, a_match())
    _sign_in(monkeypatch, "acct-ada")
    results.current_account_id()

    _sign_in(monkeypatch, None)
    results.record(results.LOCAL_ACCOUNT, a_match())
    results._adoption_settled.clear()  # a fresh process
    _sign_in(monkeypatch, "acct-bob")
    assert results.current_account_id() == "acct-bob"

    assert results.latest("acct-bob") is None
    assert results.latest("acct-ada") is not None
    assert results.latest(results.LOCAL_ACCOUNT) is not None


def test_a_collision_keeps_the_accounts_own_row(monkeypatch):
    """`hassault_drop_claims` is keyed (account, level): if both ids claimed level
    2, the account's claim stays and the stand-in's is not merged over it."""
    with skin_manager._conn() as conn:
        for account, instance in (("acct-ada", "mine"), (results.LOCAL_ACCOUNT, "old")):
            conn.execute(
                "INSERT INTO hassault_drop_claims VALUES (?, 2, ?, 0)",
                (account, instance),
            )
    _sign_in(monkeypatch, "acct-ada")
    results.current_account_id()
    with skin_manager._conn() as conn:
        rows = dict(
            conn.execute(
                "SELECT account_id, instance_id FROM hassault_drop_claims"
            ).fetchall()
        )
    assert rows == {"acct-ada": "mine", results.LOCAL_ACCOUNT: "old"}
