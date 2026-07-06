"""In-process tests for the game server: hub dispatch + authoritative referee.

Drives the `GameHub` directly through a fake connection (no socket/uvicorn), which
is the same code path the `/game-ws` endpoint uses. Follows the repo convention of
`asyncio.run` inside synchronous test functions (see test_network_collab.py).
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.games_server import models
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
    assert conn.last(models.AUTHED)["account_id"] == name
    return conn, session


async def _seat_two(hub: GameHub):
    a_conn, a = await _auth(hub, "alice")
    b_conn, b = await _auth(hub, "bob")
    await hub.handle(a, {"type": models.CREATE_TABLE, "game_id": "tictactoe"})
    table_id = a_conn.last(models.TABLE)["table"]["id"]
    await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})
    return (a_conn, a), (b_conn, b), table_id


async def _move(hub: GameHub, session, cell: str) -> None:
    await hub.handle(
        session, {"type": models.ACTION, "game_id": "tictactoe", "action_id": cell}
    )


def test_auth_required_before_lobby() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn = FakeConn()
        session = hub.connect(conn)
        await hub.handle(session, {"type": models.LIST_TABLES})
        assert conn.last(models.ERROR)["code"] == "unauthed"

    asyncio.run(go())


def test_empty_token_rejected() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        conn = FakeConn()
        session = hub.connect(conn)
        await hub.handle(session, {"type": models.AUTH, "token": ""})
        assert conn.last(models.ERROR)["code"] == "auth"

    asyncio.run(go())


def test_full_table_starts_and_prompts_first_seat_only() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        (a_conn, _a), (b_conn, _b), _tid = await _seat_two(hub)
        # X (alice, seat 0) is on the clock; O (bob) is not yet.
        assert a_conn.last(models.YOUR_TURN) is not None
        assert a_conn.last(models.YOUR_TURN)["seat"] == 0
        assert b_conn.last(models.YOUR_TURN) is None
        # Both saw the opening public state.
        assert a_conn.last(models.PUBLIC_STATE)["state"]["board"] == [None] * 9

    asyncio.run(go())


def test_out_of_turn_and_illegal_moves_rejected() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        (_a_conn, a), (b_conn, b), _tid = await _seat_two(hub)
        # Bob moving out of turn.
        await _move(hub, b, "0")
        assert b_conn.last(models.ERROR)["code"] == "not_your_turn"
        # Alice plays 0; now bob may move, but re-taking cell 0 is illegal.
        await _move(hub, a, "0")
        await _move(hub, b, "0")
        assert b_conn.last(models.ERROR)["code"] == "illegal_move"
        # After the rejection bob is re-prompted, not skipped.
        assert b_conn.last(models.YOUR_TURN)["seat"] == 1

    asyncio.run(go())


def test_same_account_two_devices_can_play() -> None:
    """Two connections authenticated with the *same* token (one account signed in on
    two computers) are two distinct players: they take both seats and the game starts.
    Regression for the account-keyed hub that let the second device evict the first."""

    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        # Same token → same account, but two separate connections/sessions.
        a_conn, a = await _auth(hub, "solo")
        b_conn, b = await _auth(hub, "solo")
        assert a.session_id != b.session_id
        await hub.handle(a, {"type": models.CREATE_TABLE, "game_id": "tictactoe"})
        table_id = a_conn.last(models.TABLE)["table"]["id"]
        await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})
        # Both seats filled → the game started and seat 0 (device A) is prompted.
        assert a_conn.last(models.YOUR_TURN) is not None
        assert a_conn.last(models.YOUR_TURN)["seat"] == 0
        # The lobby still advertises the account id in both seats.
        table = a_conn.last(models.TABLE)["table"]
        assert table["seats"] == ["solo", "solo"]

    asyncio.run(go())


def test_game_plays_to_a_win() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        (a_conn, a), (b_conn, b), _tid = await _seat_two(hub)
        # Alice (X) takes the top row; bob blocked elsewhere.
        for actor, cell in [(a, "0"), (b, "3"), (a, "1"), (b, "4"), (a, "2")]:
            await _move(hub, actor, cell)

        over = a_conn.last(models.GAME_OVER)
        assert over is not None
        assert over["winner"] == 0
        # returns keys may be JSON-stringified once serialized; accept both.
        assert {int(k): v for k, v in over["returns"].items()} == {0: 1.0, 1: -1.0}
        assert b_conn.last(models.GAME_OVER) is not None

    asyncio.run(go())
