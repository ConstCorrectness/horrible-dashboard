"""The ranked queue: rating-window pairing, bot backfill, placement fast-path,
and tier-gated difficulties."""

from __future__ import annotations

import asyncio
from typing import Any

from backend.games_server import models, store
from backend.games_server.hub import GameHub


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


async def _auth(hub: GameHub, name: str):
    conn = FakeConn()
    session = hub.connect(conn)
    await hub.handle(session, {"type": models.AUTH, "token": name})
    return conn, session


def _give_rating(account: str, game: str, rating: float, placed: bool = True) -> None:
    store.init_db()
    with store.get_conn() as conn:
        store._write_rating(
            conn,
            {
                "account_id": account,
                "game_id": game,
                "rating": rating,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "games": 10,
                "placement_games": store.PLACEMENT_GAMES if placed else 0,
            },
        )


def test_close_ratings_pair_immediately() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _give_rating("alice", "tictactoe", 1200)
        _give_rating("bob", "tictactoe", 1230)
        a_conn, a = await _auth(hub, "alice")
        b_conn, b = await _auth(hub, "bob")
        await hub.handle(a, {"type": models.QUEUE_JOIN, "game_id": "tictactoe"})
        await hub.handle(b, {"type": models.QUEUE_JOIN, "game_id": "tictactoe"})
        found_a = a_conn.last(models.MATCH_FOUND)
        found_b = b_conn.last(models.MATCH_FOUND)
        assert found_a is not None and found_b is not None
        assert found_a["opponent"]["account_id"] == "bob"
        assert found_b["opponent"]["account_id"] == "alice"
        # The table exists, both seated, game started.
        table = hub._tables[found_a["table_id"]]
        assert table.ruleset.rated
        assert a_conn.last(models.MATCH_INFO) is not None

    asyncio.run(go())


def test_distant_ratings_wait_for_the_window() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _give_rating("alice", "tictactoe", 1200)
        _give_rating("bob", "tictactoe", 1600)
        a_conn, a = await _auth(hub, "alice")
        _b_conn, b = await _auth(hub, "bob")
        await hub.handle(a, {"type": models.QUEUE_JOIN, "game_id": "tictactoe"})
        await hub.handle(b, {"type": models.QUEUE_JOIN, "game_id": "tictactoe"})
        assert a_conn.last(models.MATCH_FOUND) is None  # 400 apart > ±75

    asyncio.run(go())


def test_placement_backfills_with_a_bot_instantly() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, a = await _auth(hub, "newbie")
        await hub.handle(
            a,
            {"type": models.QUEUE_JOIN, "game_id": "tictactoe", "placement": True},
        )
        found = a_conn.last(models.MATCH_FOUND)
        assert found is not None
        assert found["opponent"] is None  # bot fills after the push
        table = hub._tables[found["table_id"]]
        assert any(
            (table.account_of.get(s) or "").startswith("bot:") for s in table.seats if s
        )
        # The bot seat filled → the match actually started.
        assert a_conn.last(models.MATCH_INFO) is not None

    asyncio.run(go())


def test_bot_backfill_after_deadline(monkeypatch) -> None:
    async def go() -> None:
        monkeypatch.setenv("GAMES_QUEUE_BOT_S", "0")
        hub = GameHub(move_timeout_s=0)
        _give_rating("vet", "tictactoe", 1300)
        a_conn, a = await _auth(hub, "vet")
        await hub.handle(a, {"type": models.QUEUE_JOIN, "game_id": "tictactoe"})
        # join() sweeps once; with a 0s deadline the bot backfill fires there.
        found = a_conn.last(models.MATCH_FOUND)
        assert found is not None

    asyncio.run(go())


def test_hard_difficulty_is_tier_locked() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _give_rating("bronzey", "tictactoe", 1000)  # bronze, placed
        a_conn, a = await _auth(hub, "bronzey")
        await hub.handle(
            a,
            {"type": models.QUEUE_JOIN, "game_id": "tictactoe", "difficulty": "hard"},
        )
        assert a_conn.last(models.ERROR)["code"] == "tier_locked"

        # A gold player gets in.
        _give_rating("goldy", "tictactoe", 1300)
        g_conn, g = await _auth(hub, "goldy")
        await hub.handle(
            g,
            {"type": models.QUEUE_JOIN, "game_id": "tictactoe", "difficulty": "hard"},
        )
        assert g_conn.last(models.ERROR) is None

    asyncio.run(go())


def test_queue_leave_and_disconnect_clear_the_slot() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _give_rating("alice", "tictactoe", 1200)
        _a_conn, a = await _auth(hub, "alice")
        await hub.handle(a, {"type": models.QUEUE_JOIN, "game_id": "tictactoe"})
        assert a.session_id in hub.matchmaker._entries
        await hub.handle(a, {"type": models.QUEUE_LEAVE})
        assert a.session_id not in hub.matchmaker._entries

    asyncio.run(go())
