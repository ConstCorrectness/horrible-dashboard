"""Integration: two auto-playing 'clients' driven by a deterministic stub policy
play a full game through the referee to a decisive result.

Drives the `GameHub` in-process (no sockets) but, unlike test_games_server's
scripted game, nothing scripts the moves — a stub policy (pick the first legal
action) responds to each `your_turn`, exercising the full referee loop end to end.
"""

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
        for m in reversed(self.messages):
            if m.get("type") == mtype:
                return m
        return None


async def _auth(hub: GameHub, name: str):
    conn = FakeConn()
    session = hub.connect(conn)
    await hub.handle(session, {"type": models.AUTH, "token": name})
    return conn, session


async def _run_stub_game(game_id: str) -> dict[str, Any]:
    """Seat two stub players and let them auto-respond to every your_turn."""
    hub = GameHub(move_timeout_s=0)
    players = {name: await _auth(hub, name) for name in ("alice", "bob")}
    (a_conn, a) = players["alice"]
    (_b_conn, b) = players["bob"]
    await hub.handle(a, {"type": models.CREATE_TABLE, "game_id": game_id})
    table_id = a_conn.last(models.TABLE)["table"]["id"]
    await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})

    processed = {name: 0 for name in players}
    for _ in range(200):  # generous bound; TTT resolves in <= 9 plies
        progressed = False
        for name, (conn, session) in players.items():
            while processed[name] < len(conn.messages):
                msg = conn.messages[processed[name]]
                processed[name] += 1
                if msg.get("type") == models.YOUR_TURN:
                    # Stub policy: the first legal action, deterministically.
                    first = msg["legal_actions"][0]["id"]
                    await hub.handle(
                        session,
                        {
                            "type": models.ACTION,
                            "game_id": msg["game_id"],
                            "action_id": first,
                        },
                    )
                    progressed = True
        if any(c.last(models.GAME_OVER) for c, _ in players.values()):
            break
        if not progressed:
            break

    over = a_conn.last(models.GAME_OVER)
    assert over is not None, "game did not reach a terminal state"
    return over


def test_stub_selfplay_records_result_to_ladder() -> None:
    asyncio.run(_run_stub_game("tictactoe"))
    # The referee's on_result callback persisted the finished game to the ladder.
    rows = {r["account_id"]: r for r in store.leaderboard("tictactoe")}
    assert set(rows) == {"alice", "bob"}
    assert rows["alice"]["games"] == 1 and rows["bob"]["games"] == 1


def test_stub_selfplay_tictactoe_reaches_a_terminal_result() -> None:
    over = asyncio.run(_run_stub_game("tictactoe"))
    returns = {int(k): v for k, v in over["returns"].items()}
    # Either someone won (+1/-1) or it was a draw (0/0) — always zero-sum.
    assert set(returns.keys()) == {0, 1}
    assert sum(returns.values()) == 0.0
    if over["winner"] is not None:
        assert returns[over["winner"]] == 1.0
