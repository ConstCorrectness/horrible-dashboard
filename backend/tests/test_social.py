"""The Plaza (human social layer) tests: presence + rooms, movement, speech
bubbles, the global roster + activity, friendships, and gamified profiles/XP.

The store tests point HORRIBLE_DATA_DIR at a tmp dir so each run gets a fresh
`game_server.db` and never touches a developer's real ladder."""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest

from backend.games_server import models
from backend.games_server.hub import GameHub
from backend.games_server.social import DEFAULT_ROOM, ROOM_IDS, SAY_MAX_CHARS


class FakeConn:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.messages.append(msg)

    def last(self, mtype: str) -> dict[str, Any] | None:
        for msg in reversed(self.messages):
            if msg.get("type") == mtype:
                return msg
        return None


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A fresh game_server.db in a tmp dir, with store's module reloaded so its
    module-level state (none here, but future-proof) starts clean."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.games_server import store as store_module

    importlib.reload(store_module)
    store_module.init_db()
    return store_module


async def _plaza(hub: GameHub, token: str, name: str = "", avatar: str = ""):
    conn = FakeConn()
    session = hub.connect(conn)
    await hub.handle(session, {"type": models.AUTH, "token": token})
    await hub.handle(
        session, {"type": models.SOCIAL_JOIN, "name": name, "avatar": avatar}
    )
    return conn, session


# ---- presence + rooms ------------------------------------------------------


def test_join_lands_in_default_room_with_snapshot(store) -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn, _ = await _plaza(hub, "alice", name="Alice", avatar="🦸")
        joined = conn.last(models.SOCIAL_JOINED)
        assert joined is not None
        assert joined["you"]["name"] == "Alice"
        assert joined["room"] == DEFAULT_ROOM
        assert [r["id"] for r in joined["rooms"]] == list(ROOM_IDS)
        # A fresh join also gets its profile + friends immediately.
        assert conn.last(models.PROFILE) is not None
        assert conn.last(models.FRIENDS) is not None

    asyncio.run(go())


def test_roster_lists_everyone_online(store) -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, _ = await _plaza(hub, "alice", name="Alice")
        await _plaza(hub, "bob", name="Bob")
        roster = a_conn.last(models.SOCIAL_ROSTER)
        assert roster is not None
        names = {p["name"] for p in roster["online"]}
        assert names == {"Alice", "Bob"}

    asyncio.run(go())


def test_room_state_is_scoped_to_the_room(store) -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, alice = await _plaza(hub, "alice", name="Alice")
        b_conn, bob = await _plaza(hub, "bob", name="Bob")
        # Bob walks off to the arcade; the plaza's room state loses him.
        await hub.handle(bob, {"type": models.SOCIAL_ROOM, "room": "arcade"})
        plaza_state = a_conn.last(models.SOCIAL_STATE)
        assert plaza_state["room"] == DEFAULT_ROOM
        assert [o["name"] for o in plaza_state["occupants"]] == ["Alice"]
        # Bob's own view is the arcade, where he's alone.
        bob_state = b_conn.last(models.SOCIAL_STATE)
        assert bob_state["room"] == "arcade"
        assert [o["name"] for o in bob_state["occupants"]] == ["Bob"]

    asyncio.run(go())


def test_say_pops_a_bubble_capped_and_room_local(store) -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, alice = await _plaza(hub, "alice", name="Alice")
        b_conn, bob = await _plaza(hub, "bob", name="Bob")
        await hub.handle(bob, {"type": models.SOCIAL_ROOM, "room": "lounge"})
        await hub.handle(alice, {"type": models.SOCIAL_SAY, "text": "y" * 1000})
        # Alice's bubble shows in the plaza (hers), capped in length…
        bubbles = a_conn.last(models.SOCIAL_STATE)["bubbles"]
        assert bubbles and len(bubbles[-1]["text"]) == SAY_MAX_CHARS
        # …and never reaches Bob over in the lounge.
        assert all(
            "y" not in bub["text"]
            for bub in (b_conn.last(models.SOCIAL_STATE)["bubbles"])
        )

    asyncio.run(go())


def test_disconnect_removes_presence(store) -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _, alice = await _plaza(hub, "alice", name="Alice")
        assert "alice" in hub.social._presence
        await hub.disconnect(alice)
        assert "alice" not in hub.social._presence

    asyncio.run(go())


def test_seating_a_table_updates_roster_activity(store) -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn, alice = await _plaza(hub, "alice", name="Alice")
        await hub.handle(alice, {"type": models.CREATE_TABLE, "game_id": "tictactoe"})
        # set_activity is scheduled as a task; let it run.
        await asyncio.sleep(0)
        roster = conn.last(models.SOCIAL_ROSTER)
        me = next(p for p in roster["online"] if p["account_id"] == "alice")
        assert "Tic-Tac-Toe" in me["activity"]

    asyncio.run(go())


def test_invite_hosts_a_table_and_notifies_target(store) -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _, alice = await _plaza(hub, "alice", name="Alice")
        b_conn, _ = await _plaza(hub, "bob", name="Bob")
        await hub.handle(
            alice,
            {
                "type": models.SOCIAL_INVITE,
                "account_id": "bob",
                "game_id": "tictactoe",
            },
        )
        invited = b_conn.last(models.SOCIAL_INVITED)
        assert invited is not None
        assert invited["from_id"] == "alice"
        assert invited["game_id"] == "tictactoe"
        assert invited["table_id"] in hub._tables

    asyncio.run(go())


# ---- friendships (store) ---------------------------------------------------


def test_friend_request_accept_and_list(store) -> None:
    assert store.request_friend("alice", "bob") == "pending"
    # Pending shows as incoming for Bob, not for Alice.
    assert [p["account_id"] for p in store.list_pending("bob")] == ["alice"]
    assert store.list_pending("alice") == []
    assert store.accept_friend("bob", "alice") is True
    assert [f["account_id"] for f in store.list_friends("alice")] == ["bob"]
    assert [f["account_id"] for f in store.list_friends("bob")] == ["alice"]
    assert store.list_pending("bob") == []


def test_mutual_request_auto_accepts(store) -> None:
    assert store.request_friend("alice", "bob") == "pending"
    # Bob asking back is mutual intent → instant friendship (no accept needed).
    assert store.request_friend("bob", "alice") == "accepted"
    assert [f["account_id"] for f in store.list_friends("alice")] == ["bob"]


def test_only_addressee_can_accept_and_remove_works(store) -> None:
    store.request_friend("alice", "bob")
    # The requester can't accept their own request.
    assert store.accept_friend("alice", "bob") is False
    assert store.accept_friend("bob", "alice") is True
    store.remove_friend("alice", "bob")
    assert store.list_friends("alice") == []
    assert store.list_friends("bob") == []


# ---- gamified profiles / XP ------------------------------------------------


def test_profile_defaults_and_avatar_persist(store) -> None:
    p = store.get_profile("alice")
    assert p["level"] == 1 and p["xp"] == 0 and p["avatar"] == "🙂"
    store.upsert_profile("alice", avatar="🦸", bio="gg")
    p = store.get_profile("alice")
    assert p["avatar"] == "🦸" and p["bio"] == "gg"


def test_xp_grants_level_up(store) -> None:
    assert store.level_for_xp(0) == 1
    assert store.level_for_xp(30) == 2
    prof = store.add_xp("alice", 100)
    assert prof["xp"] == 100 and prof["level"] >= 3
    # next_level_xp is the threshold for the level above the current one.
    assert prof["next_level_xp"] is not None and prof["next_level_xp"] > prof["xp"]


def test_finished_game_awards_xp_to_both_seats(store) -> None:
    # Winner (seat 0) earns more than the loser (seat 1), but both gain.
    store.record_result(
        "tictactoe", "t1", ["alice", "bob"], {0: 1.0, 1: -1.0}, winner=0
    )
    assert store.get_profile("alice")["xp"] == 20
    assert store.get_profile("bob")["xp"] == 5
