"""Duels: two forks of one round, judged blind, ranked by replayed Elo.

What has to hold:

1. **Both sides really run**, on the same round, each with its own edits, through
   the ordinary fork path (so tools are simulated and each side is a fork edge).
2. **The vote is blind.** Which contestant is shown as A is random; a pane that
   always put the first-picked model on the left would leak the answer.
3. **Ratings are a pure function of the votes**: replayed in vote order from a
   fresh start, so changing or deleting a vote leaves nothing behind.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.modules.agent import providers as P
from backend.modules.agent.models import AgentConfig
from backend.modules.agentpedia import duel, store
from backend.modules.agentpedia.models import (
    Duel,
    DuelRequest,
    DuelSide,
    ForkEdit,
)
from backend.modules.interpretability import recorder
from backend.modules.interpretability.models import (
    ContextBlock,
    RoundSnapshot,
    ToolEntry,
    TurnSnapshot,
)


@pytest.fixture(autouse=True)
def clean():
    recorder.clear()
    store.clear()
    yield
    recorder.clear()
    store.clear()


def _block(kind: str, role: str, content: str) -> ContextBlock:
    return ContextBlock(
        kind=kind,
        role=role,
        label=kind.title(),
        content=content,
        tokens=len(content) // 4,
    )


@pytest.fixture
def parent() -> TurnSnapshot:
    turn = TurnSnapshot(
        turnId="parent1",
        agentId="main",
        model="base-model",
        provider="ollama",
        startedAt=1000.0,
        rounds=[
            RoundSnapshot(
                round=0,
                blocks=[
                    _block("system", "system", "be good"),
                    _block("user", "user", "name a colour"),
                ],
                tools=[ToolEntry(name="ui.open_pane", group="ui", tokens=20)],
                activeGroups=["ui"],
            )
        ],
    )
    recorder._turns.append(turn)
    return turn


@pytest.fixture
def models(monkeypatch):
    """A node whose model answers with its own name, so each side is identifiable
    whatever order the two concurrent runs reach it in."""
    from backend.modules.agent import offline_conn
    from backend.modules.agent import routes as agent_routes

    monkeypatch.setattr(offline_conn, "live_agent_tools", lambda: [])
    monkeypatch.setattr(
        agent_routes,
        "_load_config",
        lambda: AgentConfig(model="base-model", provider="ollama"),
    )
    seen: list[tuple[str, float | None]] = []

    async def chat_stream(
        client, info, endpoint, model, messages, tools, on_delta, **kw
    ):
        seen.append((model, kw.get("temperature")))
        await asyncio.sleep(0)  # let the other side interleave
        text = f"answer from {model}"
        return P.ChatResult(
            assistant_message={"role": "assistant", "content": text},
            tool_calls=[],
            content=text,
        )

    monkeypatch.setattr(P, "chat_stream", chat_stream)
    return seen


def _contestants(*models: str) -> list[list[ForkEdit]]:
    return [[ForkEdit(op="set_model", name=m)] for m in models]


def test_both_sides_run_on_the_same_round_and_are_recorded(parent, models):
    d = asyncio.run(
        duel.run(
            DuelRequest(turn_id="parent1", contestants=_contestants("small", "large")),
            rng=lambda: 0.9,  # no swap: first contestant is A
        )
    )
    assert {m for m, _ in models} == {"small", "large"}
    assert (d.a.label, d.b.label) == ("small", "large")
    assert d.a.answer == "answer from small"
    assert d.b.answer == "answer from large"
    assert d.vote is None
    # Each side is an ordinary fork edge, and the pairing is stored.
    assert {f.fork_turn_id for f in store.list_forks()} == {
        d.a.fork_turn_id,
        d.b.fork_turn_id,
    }
    assert store.get_duel(d.id) == d


def test_which_contestant_is_shown_as_a_is_a_coin_flip(parent, models):
    request = DuelRequest(turn_id="parent1", contestants=_contestants("small", "large"))
    kept = asyncio.run(duel.run(request, rng=lambda: 0.9))
    swapped = asyncio.run(duel.run(request, rng=lambda: 0.1))
    assert (kept.a.label, kept.b.label) == ("small", "large")
    assert (swapped.a.label, swapped.b.label) == ("large", "small")


def test_identical_contestants_and_bad_edits_are_refused_before_anything_runs(
    parent, models
):
    with pytest.raises(duel.DuelError, match="same edits"):
        asyncio.run(
            duel.run(DuelRequest(turn_id="parent1", contestants=_contestants("x", "x")))
        )
    with pytest.raises(duel.DuelError):
        asyncio.run(
            duel.run(
                DuelRequest(
                    turn_id="parent1",
                    contestants=[
                        [ForkEdit(op="set_model", name="x")],
                        [ForkEdit(op="drop_tool", name="no.such.tool")],
                    ],
                )
            )
        )
    # Neither side of either request reached the model.
    assert models == []


def test_labels_name_the_configuration_not_just_the_model():
    assert duel.contestant_label("m", []) == "m"
    assert (
        duel.contestant_label(
            "m",
            [
                ForkEdit(op="set_temperature", value=0.7),
                ForkEdit(op="set_provider", name="openrouter"),
                ForkEdit(op="drop_tool", name="web.search"),
                ForkEdit(op="set_system", content="be terse"),
            ],
        )
        == "openrouter/m · T=0.7 · −web.search · custom system prompt"
    )


def _voted(a: str, b: str, vote: str, at: float) -> Duel:
    side = lambda label: DuelSide(fork_turn_id=f"f-{label}-{at}", label=label)  # noqa: E731
    return Duel(id=f"d{at}", turn_id="t", a=side(a), b=side(b), vote=vote, voted_at=at)


def test_elo_is_replayed_from_the_votes():
    board = duel.leaderboard(
        [
            _voted("big", "small", "a", 1),
            _voted("small", "big", "b", 2),
            _voted("big", "mid", "tie", 3),
            _voted("mid", "small", "both_bad", 4),
        ]
    )
    by = {c.label: c for c in board.contestants}
    assert board.voted == 4
    assert [c.label for c in board.contestants][0] == "big"
    assert (by["big"].wins, by["big"].losses, by["big"].ties) == (2, 0, 1)
    assert (by["small"].wins, by["small"].losses, by["small"].both_bad) == (0, 2, 1)
    # Two wins from 1000 apiece: +16, then +~14.5; the tie against an unrated
    # 1000 costs the leader a little.
    assert by["big"].rating == pytest.approx(1000 + 16 + 14.53 - 1.45, abs=0.2)
    # Elo is zero-sum, and `both_bad` moves no rating.
    assert sum(c.rating for c in board.contestants) == pytest.approx(3000, abs=0.2)
    assert by["mid"].rating == pytest.approx(1000 + 1.45, abs=0.2)


def test_a_duel_between_two_draws_of_one_configuration_moves_nothing():
    board = duel.leaderboard([_voted("same", "same", "a", 1)])
    assert board.contestants[0].rating == 1000.0
    assert board.contestants[0].games == 0


def test_the_routes_run_vote_rank_and_forget(parent, models):
    from backend.app import app

    client = TestClient(app)
    created = client.post(
        "/api/agentpedia/duels",
        json={
            "turn_id": "parent1",
            "contestants": [
                [{"op": "set_model", "name": "small"}],
                [{"op": "set_model", "name": "large"}],
            ],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert {body["a"]["label"], body["b"]["label"]} == {"small", "large"}
    winner = "a" if body["a"]["label"] == "large" else "b"

    voted = client.post(
        f"/api/agentpedia/duels/{body['id']}/vote", json={"vote": winner}
    )
    assert voted.json()["vote"] == winner

    board = client.get("/api/agentpedia/leaderboard").json()
    assert [c["label"] for c in board["contestants"]] == ["large", "small"]
    assert board["voted"] == 1

    assert client.get("/api/agentpedia/duels").json()["duels"][0]["id"] == body["id"]
    assert (
        client.post("/api/agentpedia/duels/nope/vote", json={"vote": "a"}).status_code
        == 404
    )
    bad = client.post(
        "/api/agentpedia/duels",
        json={"turn_id": "parent1", "contestants": [[], []]},
    )
    assert bad.status_code == 400

    assert client.delete(f"/api/agentpedia/duels/{body['id']}").json() == {
        "deleted": True
    }
    assert client.get("/api/agentpedia/leaderboard").json()["contestants"] == []
