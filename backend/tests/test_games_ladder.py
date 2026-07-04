"""The persistent ladder: accounts, ELO updates on results, leaderboard ordering."""

from __future__ import annotations

from backend.games_server import store


def _lb_by_account(game_id: str) -> dict[str, dict]:
    return {r["account_id"]: r for r in store.leaderboard(game_id)}


def test_win_moves_ratings_and_records_wl() -> None:
    store.record_result("tictactoe", "t1", ["a", "b"], {0: 1.0, 1: -1.0}, winner=0)
    lb = _lb_by_account("tictactoe")
    assert lb["a"]["rating"] > store.BASE_RATING
    assert lb["b"]["rating"] < store.BASE_RATING
    # Zero-sum ELO: what the winner gains, the loser loses.
    assert round(lb["a"]["rating"] - store.BASE_RATING, 6) == round(
        store.BASE_RATING - lb["b"]["rating"], 6
    )
    assert lb["a"]["wins"] == 1 and lb["a"]["losses"] == 0
    assert lb["b"]["losses"] == 1 and lb["b"]["wins"] == 0


def test_draw_between_equals_keeps_ratings() -> None:
    store.record_result("tictactoe", "t1", ["a", "b"], {0: 0.0, 1: 0.0}, winner=None)
    lb = _lb_by_account("tictactoe")
    assert lb["a"]["rating"] == store.BASE_RATING == lb["b"]["rating"]
    assert lb["a"]["draws"] == 1 and lb["b"]["draws"] == 1


def test_leaderboard_orders_by_rating_and_uses_display_name() -> None:
    # A registered account shows its display name; a bare (dev) id shows the id.
    store.upsert_account("github", "42", "octocat")
    store.record_result(
        "tictactoe", "t1", ["github:42", "dev-bob"], {0: 1.0, 1: -1.0}, 0
    )
    rows = store.leaderboard("tictactoe")
    assert [r["account_id"] for r in rows] == ["github:42", "dev-bob"]  # winner first
    assert rows[0]["display_name"] == "octocat"
    assert rows[1]["display_name"] == "dev-bob"  # no account row -> falls back to id


def test_multiplayer_result_logged_without_rating() -> None:
    # 3 seats: ELO is 2-player only for now, so no rating rows, but the game is logged.
    store.record_result("poker", "t1", ["a", "b", "c"], {0: 2.0, 1: -1.0, 2: -1.0}, 0)
    assert store.leaderboard("poker") == []
