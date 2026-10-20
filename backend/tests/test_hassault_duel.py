"""P7: the duel, and the rating it is the only source of.

Each test names a rule from `modes/duel.py` or `games_server/hassault_rating.py`:
a duel ends (kills, time, forfeit), both players are rated against each other's
pre-match rating, a match is rated once however often its end is reported, the
queue pairs by rating and refuses guests, and the node no longer writes a
rating of its own.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.modules.hassault import modes
from backend.modules.hassault.match import MatchServer
from backend.modules.hassault.modes import duel


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))


def duel_room():
    room = MatchServer(record_replays=False).create("hd_pit", mode="duel")
    a = room.add("a", None, team=0)
    b = room.add("b", None, team=1)
    return room, a, b


# ---- the mode --------------------------------------------------------------------


def test_a_duel_waits_for_both_seats_then_goes_live():
    room = MatchServer(record_replays=False).create("hd_pit", mode="duel")
    room.add("a", None, team=0)
    room.simulate(0.05)
    assert room.mode.phase == duel.WARMUP
    room.add("b", None, team=1)
    room.simulate(0.05)
    assert room.mode.phase == duel.LIVE
    assert modes.is_mode("duel")


def test_first_to_the_kill_limit_wins_and_the_end_is_reported_once():
    room, a, b = duel_room()
    ended: list[dict] = []
    room.on_over = lambda r, summary: ended.append(summary)
    room.simulate(0.05)
    room.scores[0] = duel.KILLS_TO_WIN
    room.simulate(0.05)
    room.simulate(0.05)
    assert room.mode.phase == duel.OVER
    assert len(ended) == 1
    assert ended[0]["winner"] == 0 and ended[0]["reason"] == "kills"
    assert set(ended[0]["seats"]) == {a.id, b.id}
    # Over is over: no damage, no respawn.
    assert room.mode.damage_scale(room, a, b) == 0.0


def test_the_clock_decides_and_equal_is_a_draw():
    room, *_ = duel_room()
    ended: list[dict] = []
    room.on_over = lambda r, s: ended.append(s)
    room.simulate(0.05)
    room.mode.remaining = 0.01
    room.simulate(0.05)
    assert ended[0]["winner"] == -1 and ended[0]["reason"] == "time"


def test_leaving_a_live_duel_forfeits_it():
    room, a, b = duel_room()
    ended: list[dict] = []
    room.on_over = lambda r, s: ended.append(s)
    room.simulate(0.05)
    room.remove(b.id)
    assert ended[0]["winner"] == 0 and ended[0]["reason"] == "forfeit"
    assert set(ended[0]["seats"]) == {a.id, b.id}, "the leaver is still a seat to rate"


# ---- the rating -------------------------------------------------------------------


def test_both_players_move_against_each_others_pre_match_rating():
    from backend.games_server import hassault_rating

    out = hassault_rating.rate_match("m1", "ada", "bob", 1.0)
    assert out["ada"]["after"] > out["ada"]["before"] == 1500.0
    assert out["bob"]["after"] < out["bob"]["before"] == 1500.0
    ada = hassault_rating.rating_of("ada")
    assert (ada["games"], ada["wins"], ada["losses"]) == (1, 1, 0)
    assert ada["placed"] is False and ada["tier"] == "Unranked"
    draw = hassault_rating.rate_match("m2", "ada", "bob", 0.5)
    assert draw is not None
    assert hassault_rating.rating_of("bob")["draws"] == 1


def test_a_match_is_rated_once_however_often_it_is_reported():
    from backend.games_server import hassault_rating

    assert hassault_rating.rate_match("m1", "ada", "bob", 1.0) is not None
    assert hassault_rating.rate_match("m1", "ada", "bob", 1.0) is None
    assert hassault_rating.rating_of("ada")["games"] == 1
    assert hassault_rating.rate_match("m2", "ada", "ada", 1.0) is None


def test_the_leaderboard_lists_only_the_placed():
    from backend.games_server import hassault_rating
    from backend.modules.hassault import ranked_mmr

    for i in range(ranked_mmr.PLACEMENT_MATCHES_REQUIRED):
        hassault_rating.rate_match(f"m{i}", "ada", "bob", 1.0)
    hassault_rating.rate_match("x", "cy", "dee", 1.0)
    board = hassault_rating.leaderboard()
    assert [e["accountId"] for e in board] == ["ada", "bob"]
    assert board[0]["rank"] == 1 and board[0]["placed"] is True


def test_the_rating_is_served_publicly_and_ones_own_needs_a_token(tmp_path):
    from fastapi.testclient import TestClient

    from backend.games_server import hassault_rating
    from backend.games_server.app import app

    hassault_rating.rate_match("m1", "ada", "bob", 1.0)
    client = TestClient(app)
    assert client.get("/api/hassault/rating/ada").json()["games"] == 1
    assert client.get("/api/hassault/account/rating").status_code == 401
    mine = client.get(
        "/api/hassault/account/rating", headers={"Authorization": "Bearer ada"}
    ).json()
    assert mine["accountId"] == "ada"


# ---- the queue --------------------------------------------------------------------


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def events(conn, name):
    return [m["data"] for m in conn.websocket.sent if m.get("event") == name]


def test_two_queued_seats_are_paired_into_a_rated_duel_and_rated_when_it_ends():
    from backend.games_server import hassault_rating
    from backend.games_server.hassault_rooms import HassaultReferee, SeatConn

    referee = HassaultReferee()

    async def go() -> None:
        a = SeatConn(FakeSocket(), "ada", "ada")
        b = SeatConn(FakeSocket(), "bob", "bob")
        status = await referee.queue_duel(a)
        assert status["queued"] and status["position"] == 1
        await referee.queue_duel(b)
        welcome = events(a, "welcome")[-1]
        assert welcome["mode"]["id"] == "duel" and welcome["rated"] is True
        assert events(b, "welcome")[-1]["room"] == welcome["room"]
        room, pa = referee.server.player_for(a)
        room.simulate(0.05)
        room.scores[pa.team] = duel.KILLS_TO_WIN
        room.simulate(0.05)
        await asyncio.sleep(0.05)  # the rating goes out on its own task
        update = events(a, "rating_update")[-1]
        assert update["won"] is True and update["delta"] > 0
        assert events(b, "rating_update")[-1]["won"] is False

    asyncio.run(go())
    assert hassault_rating.rating_of("ada")["wins"] == 1


def test_a_guest_cannot_queue_and_a_far_rating_waits_for_the_window():
    from backend.games_server import hassault_rating
    from backend.games_server.hassault_rooms import HassaultReferee, SeatConn

    referee = HassaultReferee()
    # Ada is 400 points clear of a newcomer.
    for i in range(6):
        hassault_rating.rate_match(f"m{i}", "ada", f"x{i}", 1.0)
    gap = hassault_rating.rating_of("ada")["rating"] - 1500.0
    assert gap > referee.duel_window(0)

    async def go() -> None:
        with pytest.raises(ValueError):
            await referee.queue_duel(
                SeatConn(FakeSocket(), "guest_1", "g", is_guest=True)
            )
        a = SeatConn(FakeSocket(), "ada", "ada")
        b = SeatConn(FakeSocket(), "new", "new")
        await referee.queue_duel(a)
        await referee.queue_duel(b)
        assert not events(a, "welcome"), "too far apart to pair at once"
        assert referee.duel_window(10 * gap) >= gap, "but the window widens"
        referee.cancel_duel(a)
        referee.cancel_duel(b)

    asyncio.run(go())


# ---- the node no longer rates -------------------------------------------------------


def test_the_node_writes_no_rating_of_its_own():
    import inspect

    from backend.modules.hassault import channel, ranked

    assert "update_player_rating" not in inspect.getsource(channel)
    assert "update_player_rating" not in inspect.getsource(ranked)
