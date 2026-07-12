"""Replays end to end through the hub: a finished game persists its event log
(public states, actions, uploaded reasoning traces), participants can read it,
outsiders can't until a participant publishes it.

Also covers `match_info`: every seated player learns who they're up against and
the replay id their game will be saved under.
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
        for msg in reversed(self.messages):
            if msg.get("type") == mtype:
                return msg
        return None


async def _auth(hub: GameHub, name: str):
    conn = FakeConn()
    session = hub.connect(conn)
    await hub.handle(session, {"type": models.AUTH, "token": name})
    return conn, session


async def _play_full_game(hub: GameHub):
    """Alice beats Bob at tictactoe, uploading a trace before each of her moves."""
    a_conn, a = await _auth(hub, "alice")
    b_conn, b = await _auth(hub, "bob")
    await hub.handle(a, {"type": models.CREATE_TABLE, "game_id": "tictactoe"})
    table_id = a_conn.last(models.TABLE)["table"]["id"]
    await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})
    for session, cell in [(a, "0"), (b, "3"), (a, "1"), (b, "4"), (a, "2")]:
        if session is a:
            await hub.handle(
                session,
                {
                    "type": models.MOVE_TRACE,
                    "table_id": table_id,
                    "action_id": cell,
                    "steps": [{"kind": "assistant", "content": f"I take {cell}"}],
                },
            )
        await hub.handle(
            session,
            {"type": models.ACTION, "game_id": "tictactoe", "action_id": cell},
        )
    return a_conn, b_conn, table_id


def test_match_info_names_both_seats_and_the_replay() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, b_conn, _tid = await _play_full_game(hub)
        for conn in (a_conn, b_conn):
            info = conn.last(models.MATCH_INFO)
            assert info is not None
            assert [s["account_id"] for s in info["seats"]] == ["alice", "bob"]
            assert info["replay_id"]

    asyncio.run(go())


def test_finished_game_persists_a_replay_with_traces() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, _b_conn, _tid = await _play_full_game(hub)
        replay_id = a_conn.last(models.MATCH_INFO)["replay_id"]

        replay = store.get_replay(replay_id, viewer="alice")
        assert replay is not None
        assert replay["game_id"] == "tictactoe"
        assert replay["seats"] == ["alice", "bob"]
        assert replay["winner"] == 0
        kinds = {e["kind"] for e in replay["events"]}
        assert {"public_state", "action", "trace", "game_over"} <= kinds
        # Alice's reasoning made it in; the winning move's trace included (it was
        # uploaded before the action that ended the game).
        traces = [e for e in replay["events"] if e["kind"] == "trace"]
        assert all(t["seat"] == 0 for t in traces)
        assert any(t["action_id"] == "2" for t in traces)

    asyncio.run(go())


def test_replay_visibility_and_publish() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, _b_conn, _tid = await _play_full_game(hub)
        replay_id = a_conn.last(models.MATCH_INFO)["replay_id"]

        # Both participants see it; an outsider (or anonymous) doesn't.
        assert store.get_replay(replay_id, viewer="bob") is not None
        assert store.get_replay(replay_id, viewer="mallory") is None
        assert store.get_replay(replay_id, viewer=None) is None
        assert store.list_replays(public_only=True) == []
        assert [r["id"] for r in store.list_replays(account_id="alice")] == [replay_id]

        # Only a participant may publish; then anyone can watch.
        assert not store.publish_replay(replay_id, "mallory")
        assert store.publish_replay(replay_id, "alice")
        assert store.get_replay(replay_id, viewer="mallory") is not None
        assert [r["id"] for r in store.list_replays(public_only=True)] == [replay_id]

    asyncio.run(go())


def test_trace_upload_is_never_rebroadcast() -> None:
    """The info-leak invariant: an uploaded trace goes into the replay only — the
    opponent's live connection never receives any trace-carrying message."""

    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        _a_conn, b_conn, _tid = await _play_full_game(hub)
        for msg in b_conn.messages:
            assert msg.get("type") != models.MOVE_TRACE
            assert "steps" not in msg

    asyncio.run(go())
