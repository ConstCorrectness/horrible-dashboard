"""Practice bots: they play full games through the normal hub protocol, their
ratings stay pinned, and they never pollute the leaderboard."""

from __future__ import annotations

import asyncio
from typing import Any

from backend.games_server import models, store
from backend.games_server.bots import TIER_RATINGS, choose_action
from backend.games_server.hub import GameHub

import random


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


async def _wait_for(conn: FakeConn, mtype: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        msg = conn.last(mtype)
        if msg is not None:
            return msg
        await asyncio.sleep(0.02)
    raise AssertionError(f"never saw {mtype!r}")


def test_bot_plays_a_full_game_and_stays_pinned() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn = FakeConn()
        session = hub.connect(conn)
        await hub.handle(session, {"type": models.AUTH, "token": "alice"})
        await hub.handle(session, {"type": models.CREATE_TABLE, "game_id": "tictactoe"})
        table = next(iter(hub._tables.values()))
        await hub.seat_bot(table, "bronze", delay_s=0)

        # Alice answers each of her turns randomly; the bot answers its own.
        rng = random.Random(0)
        for _ in range(60):
            if conn.last(models.GAME_OVER) is not None:
                break
            turn = conn.last(models.YOUR_TURN)
            if turn is not None and turn.get("legal_actions"):
                conn.messages.remove(turn)
                await hub.handle(
                    session,
                    {
                        "type": models.ACTION,
                        "game_id": "tictactoe",
                        "action_id": rng.choice(
                            [a["id"] for a in turn["legal_actions"]]
                        ),
                    },
                )
            await asyncio.sleep(0.02)

        over = await _wait_for(conn, models.GAME_OVER)
        assert over is not None
        # The bot's rating stayed pinned; alice got a rating either way.
        bot_id = "bot:tictactoe:bronze"
        assert store.get_rating(bot_id, "tictactoe")["rating"] == TIER_RATINGS["bronze"]
        assert store.get_rating("alice", "tictactoe") is not None
        # The bot's synthetic session was released once the table wrapped.
        assert not any(
            (s.account_id or "").startswith("bot:") for s in hub._sessions.values()
        )
        # match_info marked the opposing seat as a bot.
        info = conn.last(models.MATCH_INFO)
        assert any(s["is_bot"] for s in info["seats"])

    asyncio.run(go())


def test_create_table_with_bot_tier_seats_a_practice_bot() -> None:
    """Practice-vs-bot: a bare create_table carrying `bot_tier` fills the other
    seat with a server bot and starts the match, so a solo player can test."""

    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn = FakeConn()
        session = hub.connect(conn)
        await hub.handle(session, {"type": models.AUTH, "token": "alice"})
        await hub.handle(
            session,
            {
                "type": models.CREATE_TABLE,
                "game_id": "tictactoe",
                "ruleset": {"game_id": "tictactoe", "rated": False},
                "bot_tier": "silver",
            },
        )
        info = await _wait_for(conn, models.MATCH_INFO)
        assert any(s["is_bot"] for s in info["seats"])
        table = next(iter(hub._tables.values()))
        assert table.status == "playing"

    asyncio.run(go())


def test_create_table_ignores_an_unknown_bot_tier() -> None:
    """A bogus tier is ignored — the table stays open for a human/self-play rather
    than erroring or seating a mystery bot."""

    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn = FakeConn()
        session = hub.connect(conn)
        await hub.handle(session, {"type": models.AUTH, "token": "alice"})
        await hub.handle(
            session,
            {"type": models.CREATE_TABLE, "game_id": "tictactoe", "bot_tier": "bogus"},
        )
        table = next(iter(hub._tables.values()))
        assert table.status == "open"
        assert conn.last(models.MATCH_INFO) is None

    asyncio.run(go())


def test_bot_tictactoe_takes_the_win() -> None:
    # X (the bot, seat 0) to move with two in a row: minimax must complete the line.
    board = ["X", "X", None, "O", "O", None, None, None, None]
    legal = [{"id": str(i)} for i in (2, 5, 6, 7, 8)]
    action, payload = choose_action(
        "tictactoe", "platinum", {"board": board, "turn": 0}, legal, random.Random(1)
    )
    assert action == "2"
    assert payload is None


def test_bot_solves_open_actions_with_the_baseline() -> None:
    obs = {
        "docs": [{"text": "The capital of France is Paris. Rome is in Italy."}],
        "questions": [{"id": "q1", "prompt": "What is the capital of France?"}],
    }
    legal = [{"id": "submit", "label": "submit", "params": {"payload": "answers"}}]
    action, payload = choose_action("rag_race", "bronze", obs, legal, random.Random(1))
    assert action == "submit"
    assert "Paris" in payload["q1"]
