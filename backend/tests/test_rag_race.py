"""RAG race duel tests: the simultaneous-turn engine, payload grading, the
baseline solver, and the hub/referee exchange (both seats on the clock at once).
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.games_engine.base import TERMINAL
from backend.games_engine.rag_race import (
    ANSWER_MAX_CHARS,
    DEFAULT_DATASET,
    RagRace,
)
from backend.games_server import models
from backend.games_server.hub import GameHub
from backend.modules.games.duel_solver import find_open_action, solve_answers


def _perfect(seat_answers: dict[str, str] | None = None) -> dict[str, str]:
    """A perfect payload straight from the (test-side) answer key."""
    answers = {str(q["id"]): str(q["accept"][0]) for q in DEFAULT_DATASET["questions"]}
    answers.update(seat_answers or {})
    return answers


# ---- engine ------------------------------------------------------------------


def test_both_seats_act_simultaneously() -> None:
    st = RagRace()
    assert st.current_players() == [0, 1]
    assert [a.id for a in st.legal_actions(0)] == ["submit"]
    assert [a.id for a in st.legal_actions(1)] == ["submit"]
    # Submissions can arrive in any order; a seat can't submit twice.
    st.apply_action(1, "submit", payload=_perfect())
    assert st.current_players() == [0]
    assert st.legal_actions(1) == []
    st.apply_action(0, "submit", payload={})
    assert st.current_player() == TERMINAL


def test_grading_and_returns() -> None:
    st = RagRace()
    st.apply_action(0, "submit", payload=_perfect())
    st.apply_action(1, "submit", payload={})
    n = len(DEFAULT_DATASET["questions"])
    assert st.returns() == {0: float(n), 1: float(-n)}
    pub = st.public_state()
    assert pub["scores"] == [n, 0]
    assert pub["winner"] == 0


def test_containment_grading_token_boundaries() -> None:
    st = RagRace()
    # A sentence containing the fact counts; the fact inside another token doesn't.
    st.apply_action(
        0,
        "submit",
        payload={"q-year": "the company was founded in 2041", "q-ore": "veyrite20415"},
    )
    st.apply_action(1, "submit", payload={"q-ore": "its export is veyrite."})
    pub = st.public_state()
    results = {r["id"]: r for r in pub["results"]}
    assert results["q-year"]["correct"] == [True, False]
    assert results["q-ore"]["correct"] == [False, True]  # '20415' is not 'veyrite'


def test_payload_validation_caps_and_filters() -> None:
    st = RagRace()
    st.apply_action(
        0,
        "submit",
        payload={"q-year": "x" * 1000, "not-a-question": "junk", 42: "also junk"},
    )
    assert st.answers[0] is not None
    assert len(st.answers[0]["q-year"]) == ANSWER_MAX_CHARS
    assert set(st.answers[0]) == {"q-year"}
    # A malformed (non-dict) payload grades as empty rather than crashing the table.
    st.apply_action(1, "submit", payload="the whole corpus pasted here")
    assert st.answers[1] == {}


def test_answer_key_never_leaves_until_showdown() -> None:
    st = RagRace()
    obs = st.observation(0)
    assert "accept" not in str(obs), "observation must not carry acceptable answers"
    assert "results" not in st.public_state()
    st.apply_action(0, "submit", payload={})
    assert "results" not in st.public_state()  # still racing
    st.apply_action(1, "submit", payload={})
    done = st.public_state()
    assert all(r["accept"] for r in done["results"])  # post-race reveal


# ---- baseline solver -----------------------------------------------------------


def test_baseline_solver_completes_the_bundled_set() -> None:
    st = RagRace()
    answers = solve_answers(st.observation(0))
    st.apply_action(0, "submit", payload=answers)
    st.apply_action(1, "submit", payload={})
    # Keyword-overlap extraction should ace the bundled needle set — it's the
    # skill floor, and this locks the dataset to stay baseline-solvable.
    n = len(DEFAULT_DATASET["questions"])
    assert st.public_state()["scores"][0] == n


def test_find_open_action() -> None:
    st = RagRace()
    legal = [a.to_wire() for a in st.legal_actions(0)]
    assert find_open_action(legal) is not None
    assert find_open_action([{"id": "fold", "label": "fold"}]) is None


def test_solver_handles_empty_observation() -> None:
    assert solve_answers({}) == {}


# ---- hub / referee -------------------------------------------------------------


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


def test_duel_prompts_both_seats_and_grades_payloads() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, b_conn = FakeConn(), FakeConn()
        a, b = hub.connect(a_conn), hub.connect(b_conn)
        await hub.handle(a, {"type": models.AUTH, "token": "alice"})
        await hub.handle(b, {"type": models.AUTH, "token": "bob"})
        await hub.handle(a, {"type": models.CREATE_TABLE, "game_id": "rag_race"})
        table_id = a_conn.last(models.TABLE)["table"]["id"]
        await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})

        # Simultaneous turns: BOTH seats were prompted the moment the table filled.
        turn_a = a_conn.last(models.YOUR_TURN)
        turn_b = b_conn.last(models.YOUR_TURN)
        assert turn_a is not None and turn_b is not None
        assert turn_a["seat"] == 0 and turn_b["seat"] == 1
        assert turn_a["observation"]["docs"], "the corpus rides in the observation"

        # Answers flow as ACTION payloads; the second seat may answer first.
        await hub.handle(
            b,
            {
                "type": models.ACTION,
                "game_id": "rag_race",
                "action_id": "submit",
                "payload": solve_answers(turn_b["observation"]),
            },
        )
        # Progress broadcast: seat 1 shows submitted while seat 0 still races.
        mid = a_conn.last(models.PUBLIC_STATE)["state"]
        assert mid["submitted"] == [False, True]

        await hub.handle(
            a,
            {
                "type": models.ACTION,
                "game_id": "rag_race",
                "action_id": "submit",
                "payload": {},
            },
        )
        over = a_conn.last(models.GAME_OVER)
        assert over is not None
        assert over["winner"] == 1  # the solver-armed seat beat the empty submitter
        final = a_conn.last(models.PUBLIC_STATE)["state"]
        assert final["scores"][1] > final["scores"][0]
        assert final["results"], "post-race reveal reaches both players"

    asyncio.run(go())


def test_duel_rejects_double_submit() -> None:
    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, b_conn = FakeConn(), FakeConn()
        a, b = hub.connect(a_conn), hub.connect(b_conn)
        await hub.handle(a, {"type": models.AUTH, "token": "alice"})
        await hub.handle(b, {"type": models.AUTH, "token": "bob"})
        await hub.handle(a, {"type": models.CREATE_TABLE, "game_id": "rag_race"})
        table_id = a_conn.last(models.TABLE)["table"]["id"]
        await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})
        submit = {"type": models.ACTION, "game_id": "rag_race", "action_id": "submit"}
        await hub.handle(a, {**submit, "payload": {}})
        await hub.handle(a, {**submit, "payload": _perfect()})  # too late
        assert a_conn.last(models.ERROR)["code"] == "not_your_turn"

    asyncio.run(go())
