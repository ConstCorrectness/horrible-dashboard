"""Challenge negotiation: offer → accept/decline/counter, and post-match rematch.

Drives the GameHub through fake connections, the same code path as `/game-ws`.
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
    return conn, session


def test_offer_accept_hosts_table_with_the_ruleset() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, a = await _auth(hub, "alice")
        b_conn, b = await _auth(hub, "bob")
        await hub.handle(
            a,
            {
                "type": models.CHALLENGE_OFFER,
                "to_account_id": "bob",
                "ruleset": {"game_id": "tictactoe", "best_of": 3, "rated": False},
            },
        )
        incoming = b_conn.last(models.CHALLENGE_INCOMING)
        assert incoming is not None
        assert incoming["from_id"] == "alice"
        assert incoming["ruleset"]["best_of"] == 3

        await hub.handle(
            b,
            {
                "type": models.CHALLENGE_RESPOND,
                "offer_id": incoming["offer_id"],
                "response": "accept",
            },
        )
        update = a_conn.last(models.CHALLENGE_UPDATE)
        assert update["status"] == "accepted"
        table = hub._tables[update["table_id"]]
        assert table.ruleset.best_of == 3 and table.ruleset.rated is False
        # Bob is already seated; alice's node auto-joins on the update push.
        assert b.session_id in table.seats

    asyncio.run(go())


def test_offer_decline_and_counter() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, a = await _auth(hub, "alice")
        b_conn, b = await _auth(hub, "bob")

        # Decline.
        await hub.handle(
            a,
            {
                "type": models.CHALLENGE_OFFER,
                "to_account_id": "bob",
                "ruleset": {"game_id": "tictactoe"},
            },
        )
        offer_id = b_conn.last(models.CHALLENGE_INCOMING)["offer_id"]
        await hub.handle(
            b,
            {
                "type": models.CHALLENGE_RESPOND,
                "offer_id": offer_id,
                "response": "decline",
            },
        )
        assert a_conn.last(models.CHALLENGE_UPDATE)["status"] == "declined"

        # Counter: bob proposes Bo5 back; alice receives a fresh offer.
        await hub.handle(
            a,
            {
                "type": models.CHALLENGE_OFFER,
                "to_account_id": "bob",
                "ruleset": {"game_id": "tictactoe", "best_of": 1},
            },
        )
        offer_id = b_conn.last(models.CHALLENGE_INCOMING)["offer_id"]
        await hub.handle(
            b,
            {
                "type": models.CHALLENGE_RESPOND,
                "offer_id": offer_id,
                "response": "counter",
                "ruleset": {"game_id": "tictactoe", "best_of": 5},
            },
        )
        counter = a_conn.last(models.CHALLENGE_INCOMING)
        assert counter["kind"] == "counter"
        assert counter["from_id"] == "bob"
        assert counter["ruleset"]["best_of"] == 5
        # The countered offer is answerable by alice (roles flipped).
        await hub.handle(
            a,
            {
                "type": models.CHALLENGE_RESPOND,
                "offer_id": counter["offer_id"],
                "response": "accept",
            },
        )
        assert b_conn.last(models.CHALLENGE_UPDATE)["status"] == "accepted"

    asyncio.run(go())


def test_offer_to_offline_target_errors() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, a = await _auth(hub, "alice")
        await hub.handle(
            a,
            {
                "type": models.CHALLENGE_OFFER,
                "to_account_id": "ghost",
                "ruleset": {"game_id": "tictactoe"},
            },
        )
        assert a_conn.last(models.ERROR)["code"] == "offline"

    asyncio.run(go())


def test_rematch_reoffers_the_same_ruleset() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, a = await _auth(hub, "alice")
        b_conn, b = await _auth(hub, "bob")
        await hub.handle(
            a,
            {
                "type": models.CREATE_TABLE,
                "game_id": "tictactoe",
                "ruleset": {"game_id": "tictactoe", "rated": False},
            },
        )
        table_id = a_conn.last(models.TABLE)["table"]["id"]
        await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})
        for actor, cell in [(a, "0"), (b, "3"), (a, "1"), (b, "4"), (a, "2")]:
            await hub.handle(
                actor,
                {"type": models.ACTION, "game_id": "tictactoe", "action_id": cell},
            )
        assert a_conn.last(models.GAME_OVER) is not None

        await hub.handle(b, {"type": models.REMATCH_OFFER, "table_id": table_id})
        incoming = a_conn.last(models.CHALLENGE_INCOMING)
        assert incoming["kind"] == "rematch"
        assert incoming["from_id"] == "bob"
        assert incoming["ruleset"]["rated"] is False

    asyncio.run(go())
