"""Best-of-N series: games chain on one table with seat swaps and intermission
broadcasts, and the series result persists."""

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

    def all(self, mtype: str) -> list[dict[str, Any]]:
        return [m for m in self.messages if m.get("type") == mtype]


async def _auth(hub: GameHub, name: str):
    conn = FakeConn()
    session = hub.connect(conn)
    await hub.handle(session, {"type": models.AUTH, "token": name})
    return conn, session


async def _wait_for(conn: FakeConn, mtype: str, timeout: float = 8.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        msg = conn.last(mtype)
        if msg is not None:
            return msg
        await asyncio.sleep(0.02)
    raise AssertionError(f"never saw {mtype!r}")


async def _play_until_over(hub: GameHub, conns_sessions, timeout: float = 8.0) -> None:
    """Both players answer their turns with the first legal action until the
    current game ends (works for tictactoe: X sweeps a row eventually)."""
    import random

    rng = random.Random(0)
    deadline = asyncio.get_running_loop().time() + timeout
    game_overs = {id(c): len(c.all(models.GAME_OVER)) for c, _ in conns_sessions}
    while asyncio.get_running_loop().time() < deadline:
        for conn, session in conns_sessions:
            if len(conn.all(models.GAME_OVER)) > game_overs[id(conn)]:
                return
            turn = conn.last(models.YOUR_TURN)
            if turn is not None and turn.get("legal_actions"):
                conn.messages.remove(turn)
                await hub.handle(
                    session,
                    {
                        "type": models.ACTION,
                        "game_id": turn["game_id"],
                        "action_id": rng.choice(
                            [a["id"] for a in turn["legal_actions"]]
                        ),
                    },
                )
        await asyncio.sleep(0.01)
    raise AssertionError("game never finished")


def test_bo3_chains_games_and_persists_the_series() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, a = await _auth(hub, "alice")
        b_conn, b = await _auth(hub, "bob")
        await hub.handle(
            a,
            {
                "type": models.CREATE_TABLE,
                "game_id": "tictactoe",
                "ruleset": {
                    "game_id": "tictactoe",
                    "best_of": 3,
                    "edit_phase_s": 1,
                    "rated": False,
                },
            },
        )
        table_id = a_conn.last(models.TABLE)["table"]["id"]
        await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})
        table = hub._tables[table_id]
        pair = [(a_conn, a), (b_conn, b)]

        first_replay = table.replay_id
        await _play_until_over(hub, pair)
        # Game 1 done → series_state announces the score and the intermission.
        state = await _wait_for(a_conn, models.SERIES_STATE)
        assert state["best_of"] == 3 and state["game_index"] == 1
        assert sum(state["wins"]) <= 1

        # Game 2 starts after the intermission: fresh match_info, new replay id,
        # seats swapped for first-move fairness.
        deadline = asyncio.get_running_loop().time() + 8
        while table.replay_id == first_replay:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.05)
        info2 = [m for m in a_conn.all(models.MATCH_INFO)][-1]
        assert info2["replay_id"] != first_replay
        assert [s["account_id"] for s in info2["seats"]] == ["bob", "alice"]

        # Play out the rest of the series.
        for _ in range(4):
            if table.series_done:
                break
            await _play_until_over(hub, pair)
            await asyncio.sleep(1.2)  # cover the intermission between games
        over = await _wait_for(a_conn, models.SERIES_OVER)
        assert over["best_of"] == 3
        assert sum(over["wins"]) >= 1

        # Persisted.
        with store.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM series WHERE id = ?", (table.series_id,)
            ).fetchone()
        assert row is not None
        assert row["best_of"] == 3

    asyncio.run(go())


def test_bo1_needs_no_series_ceremony() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, a = await _auth(hub, "alice")
        b_conn, b = await _auth(hub, "bob")
        await hub.handle(a, {"type": models.CREATE_TABLE, "game_id": "tictactoe"})
        table_id = a_conn.last(models.TABLE)["table"]["id"]
        await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})
        await _play_until_over(hub, [(a_conn, a), (b_conn, b)])
        await asyncio.sleep(0.1)
        assert a_conn.last(models.SERIES_STATE) is None
        assert a_conn.last(models.SERIES_OVER) is None
        assert hub._tables[table_id].series_done

    asyncio.run(go())
