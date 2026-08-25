"""Agentpedia: the join, the routes, and the three ways a wire column can be empty.

The module owns no store, so almost everything here is about whether the *join* is
right — which record answers which question, and what it says when one of them has
nothing to say. Those cases are the ones worth pinning: a stepper that renders an
empty column identically whether the agent did nothing or nothing was recording is
worse than one that refuses to guess.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.modules.agentpedia import join
from backend.modules.interpretability import recorder
from backend.modules.interpretability.models import (
    ContextBlock,
    RoundSnapshot,
    TurnSnapshot,
)
from backend.modules.telemetry.recorder import recorder as io


@pytest.fixture(autouse=True)
def clean():
    recorder.clear()
    io.clear()
    yield
    recorder.clear()
    io.clear()


@pytest.fixture
def client() -> TestClient:
    from backend.app import app

    return TestClient(app)


def _round(
    round_no: int = 0, blocks: list[ContextBlock] | None = None
) -> RoundSnapshot:
    return RoundSnapshot(
        round=round_no,
        blocks=blocks
        or [
            ContextBlock(
                kind="system",
                role="system",
                label="System",
                content="be good",
                tokens=3,
            ),
            ContextBlock(
                kind="user", role="user", label="User", content="hi", tokens=1
            ),
        ],
        tools=[],
        messageTokens=4,
        toolTokens=0,
        totalTokens=4,
        toolsSelected=0,
        toolBudget=44,
        toolsTruncated=False,
        activeGroups=[],
    )


def _turn(turn_id: str = "t1", rounds: int = 1, **fields: Any) -> TurnSnapshot:
    turn = TurnSnapshot(
        turnId=turn_id,
        agentId="main",
        agentName="Orchestrator",
        model="m",
        provider="lmstudio",
        startedAt=1000.0,
        rounds=[_round(i) for i in range(rounds)],
        **{"modelContextLength": 8192, **fields},
    )
    from backend.modules.interpretability import store

    store.save_turn(turn)
    return turn


# ── The wire join ────────────────────────────────────────────────────────────


def test_wire_matches_by_turn_and_round_not_by_time():
    """Two agents can run at once on one node. Matching by timestamp would
    interleave them into a transcript that reads as one agent behaving oddly."""
    io.record(
        source="outbound", method="POST", target="/api/chat", turn_id="t1", round=0
    )
    io.record(
        source="outbound", method="POST", target="/api/chat", turn_id="t1", round=1
    )
    io.record(source="outbound", method="POST", target="/other", turn_id="t2", round=0)

    mine = join.wire_for("t1", 0)
    assert [e.target for e in mine] == ["/api/chat"]
    assert [e.target for e in join.wire_for("t1", 1)] == ["/api/chat"]
    assert join.wire_for("t3", 0) == []


def test_inbound_is_not_part_of_the_turn():
    """The request that *started* the turn is not something the turn did, and the
    browser's own polling has no business in the middle of the model's reasoning."""
    io.record(
        source="inbound", method="POST", target="/api/agent", turn_id="t1", round=0
    )
    io.record(
        source="outbound", method="POST", target="/api/chat", turn_id="t1", round=0
    )
    assert [e.source for e in join.wire_for("t1", 0)] == ["outbound"]


def test_wire_status_tells_the_three_empties_apart():
    turn = _turn()

    # Nothing recorded at all: this turn predates the stamp, or telemetry was off.
    assert join.wire_status(turn, 0) == "unrecorded"

    # The ring holds only events newer than the turn: it has moved past it.
    io.record(source="outbound", method="GET", target="/x")  # ts = now, turn is at 1000
    assert join.wire_status(turn, 0) == "aged_out"

    # Something matched.
    assert join.wire_status(turn, 3) == "live"


# ── The flatten report ───────────────────────────────────────────────────────


def test_flatten_report_uses_the_real_normalizer():
    """The pane shows the system tier pre-flatten; the provider gets one leading
    system message. Reimplementing that rule here would let the two drift, so the
    report runs the same function the wire does."""
    blocks = [
        ContextBlock(
            kind="system", role="system", label="System", content="a", tokens=1
        ),
        ContextBlock(
            kind="guides", role="system", label="Tool guides", content="b", tokens=1
        ),
        ContextBlock(kind="user", role="user", label="User", content="c", tokens=1),
    ]
    report = join.flatten_report(_round(blocks=blocks))
    assert report.messages_in == 3
    assert report.messages_out == 2  # the two system blocks became one
    assert report.merged == ["System", "Tool guides"]


def test_a_mid_conversation_system_block_is_not_merged():
    """A nudge appended after the failure it answers keeps its place — merging it
    into the preamble would move it before the thing it is responding to."""
    blocks = [
        ContextBlock(
            kind="system", role="system", label="System", content="a", tokens=1
        ),
        ContextBlock(kind="user", role="user", label="User", content="b", tokens=1),
        ContextBlock(kind="nudge", role="system", label="Nudge", content="c", tokens=1),
    ]
    report = join.flatten_report(_round(blocks=blocks))
    assert report.merged == ["System"]
    assert report.messages_out == 3  # the nudge survives as its own message


# ── Routes ───────────────────────────────────────────────────────────────────


def test_timeline_lists_stored_turns(client: TestClient):
    _turn("t1")
    _turn("t2")
    body = client.get("/api/agentpedia/turns").json()
    assert {t["turn_id"] for t in body["turns"]} == {"t1", "t2"}
    # No trajectory dataset is capturing in a fresh data dir, and saying so is the
    # difference between "did nothing" and "nothing was recording".
    assert body["capture_on"] is False
    assert all(t["run"] is None for t in body["turns"])


def test_turn_route_joins_all_four_columns(client: TestClient):
    _turn("t1", rounds=2)
    io.record(
        source="outbound",
        method="POST",
        target="http://localhost:1234/v1/chat/completions",
        status=200,
        turn_id="t1",
        round=1,
    )

    body = client.get("/api/agentpedia/turns/t1").json()
    assert [r["round"] for r in body["rounds"]] == [0, 1]
    assert body["rounds"][0]["wire"] == []
    assert body["rounds"][1]["wire"][0]["status"] == 200
    assert body["rounds"][1]["cost"]["total_tokens"] == 4
    assert body["rounds"][1]["cost"]["window"] == 8192
    # The shown half is interpretability's own RoundSnapshot, camelCase intact —
    # the two panes render the identical object.
    assert body["rounds"][0]["shown"]["totalTokens"] == 4
    assert body["wire_status"] == "live"


def test_turn_route_reads_the_ring_before_the_table(client: TestClient):
    """A live turn is answerable before it is ever persisted, and a stored one after
    the ring drops it. Both paths, one route."""
    _turn("stored")
    assert client.get("/api/agentpedia/turns/stored").status_code == 200
    recorder.clear()
    assert client.get("/api/agentpedia/turns/stored").status_code == 200
    assert client.get("/api/agentpedia/turns/missing").status_code == 404


def test_window_pct_is_absent_when_the_window_is_unknown(client: TestClient):
    """No window means no denominator. A percentage of a guessed window would be a
    measurement of nothing, so the field stays null and the pane says so."""
    _turn("t1", modelContextLength=None, requestedNumCtx=None)
    body = client.get("/api/agentpedia/turns/t1").json()
    assert body["rounds"][0]["cost"]["window"] is None
    assert body["rounds"][0]["cost"]["window_pct"] is None


def test_a_peer_turn_has_no_rounds_and_that_is_not_an_error(client: TestClient):
    """`agent.ask_peer` runs on someone else's machine. The turn is recorded so the
    tree has no unexplained gap, but it carries no context — and the stepper must
    render that rather than 500."""
    _turn("p1", rounds=0, kind="peer", peerId="node-x")
    body = client.get("/api/agentpedia/turns/p1").json()
    assert body["kind"] == "peer" and body["rounds"] == []
