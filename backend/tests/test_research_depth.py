"""Gap-filling rounds, the verification pass, and mid-run steering.

The schema migration test matters most: `init_research_db` is `CREATE TABLE IF NOT
EXISTS`, so on an install that already has the tables the new columns would silently
never appear and every write would fail on an unknown column. A fresh-DB test can't
see that — this one builds the *old* schema first.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from backend.modules.agent import providers as P
from backend.modules.research import engine, runner as runner_mod, runstore
from backend.modules.research.runner import ResearchRunner


# --- migration --------------------------------------------------------------


def test_init_adds_columns_to_a_pre_existing_schema(monkeypatch, tmp_path) -> None:
    db = tmp_path / "app.db"
    monkeypatch.setattr(runstore, "ensure_app_db_dir", lambda: db)

    # The schema exactly as it shipped, without round/approval_mode/rounds_used.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE research_runs (id TEXT PRIMARY KEY, query TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'pending', effort TEXT NOT NULL DEFAULT 'auto', "
            "library TEXT NOT NULL DEFAULT 'default', provider TEXT, model TEXT, "
            "plan TEXT, report_artifact_id TEXT, report_source_id TEXT, error TEXT, "
            "tokens_used INTEGER NOT NULL DEFAULT 0, "
            "token_budget INTEGER NOT NULL DEFAULT 200000, "
            "cancel_requested INTEGER NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE TABLE research_steps (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, "
            "seq INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0, "
            "max_attempts INTEGER NOT NULL DEFAULT 3, input TEXT NOT NULL DEFAULT '{}', "
            "output TEXT, transcript TEXT, tokens_used INTEGER NOT NULL DEFAULT 0, "
            "error TEXT, started_at TIMESTAMP, finished_at TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO research_runs (id, query) VALUES ('old-run', 'legacy')"
        )

    runstore.init_research_db()

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        run_cols = {r["name"] for r in conn.execute("PRAGMA table_info(research_runs)")}
        step_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(research_steps)")
        }
    assert {"approval_mode", "rounds_used"} <= run_cols
    assert "round" in step_cols

    # The pre-existing row survives and reads back with the new defaults.
    old = runstore.get_run("old-run")
    assert old is not None
    assert old["approval_mode"] == "auto"
    assert old["rounds_used"] == 0


def test_init_is_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runstore, "ensure_app_db_dir", lambda: tmp_path / "app.db")
    runstore.init_research_db()
    runstore.init_research_db()  # must not raise "duplicate column name"


# --- independence assessment (pure) -----------------------------------------


_SOURCES = [
    {"title": "OpenAI blog", "url": "https://openai.com/blog/x"},
    {"title": "OpenAI docs", "url": "https://platform.openai.com/docs/y"},
    {"title": "Anthropic", "url": "https://anthropic.com/news/z"},
    {"title": "No URL paper", "url": ""},
]


def test_two_citations_to_one_publisher_is_one_source():
    # The whole point: citation count is trivially gamed by citing three pages of
    # the same site. Publishers are what independence means.
    graded = engine.assess_independence([{"claim": "c", "citations": [1, 2]}], _SOURCES)
    assert graded[0]["independent_sources"] == 1
    assert graded[0]["verdict"] == "single-sourced"


def test_two_publishers_is_supported():
    graded = engine.assess_independence([{"claim": "c", "citations": [1, 3]}], _SOURCES)
    assert graded[0]["independent_sources"] == 2
    assert graded[0]["verdict"] == "supported"


def test_no_citations_is_unsupported():
    graded = engine.assess_independence([{"claim": "c", "citations": []}], _SOURCES)
    assert graded[0]["verdict"] == "unsupported"


def test_dangling_citation_supports_nothing():
    graded = engine.assess_independence(
        [{"claim": "c", "citations": [99, 0, -1]}], _SOURCES
    )
    assert graded[0]["verdict"] == "unsupported"


def test_source_without_a_url_still_counts_once():
    graded = engine.assess_independence([{"claim": "c", "citations": [3, 4]}], _SOURCES)
    assert graded[0]["independent_sources"] == 2


# --- claim / contradiction parsing (pure) -----------------------------------


def test_parse_claims_extracts_and_clamps():
    raw = 'noise [{"claim": "a", "citations": [1, "2"]}, {"claim": "b"}] tail'
    claims = engine.parse_claims(raw, max_claims=5)
    assert claims == [
        {"claim": "a", "citations": [1, 2]},
        {"claim": "b", "citations": []},
    ]


def test_parse_claims_rejects_junk():
    with pytest.raises(ValueError):
        engine.parse_claims("no array here", max_claims=5)


def test_parse_contradictions_needs_two_sides():
    raw = (
        '[{"topic": "real", "positions": [{"source": 1, "claim": "x"}, '
        '{"source": 2, "claim": "y"}]}, '
        '{"topic": "one-sided", "positions": [{"source": 1, "claim": "z"}]}]'
    )
    out = engine.parse_contradictions(raw)
    assert [c["topic"] for c in out] == ["real"]


def test_parse_contradictions_degrades_to_none():
    # A failed contradiction check must never fail a run that has a good report.
    assert engine.parse_contradictions("model refused") == []
    assert engine.parse_contradictions('{"not": "a list"}') == []


def test_render_verification_only_reports_problems():
    rendered = engine.render_verification(
        {
            "claims": [
                {"claim": "fine", "citations": [1, 2], "verdict": "supported"},
                {"claim": "thin", "citations": [1], "verdict": "single-sourced"},
            ],
            "contradictions": [],
        }
    )
    assert "thin" in rendered
    assert "fine" not in rendered


def test_render_verification_says_so_when_clean():
    assert "no problems" in engine.render_verification(
        {"claims": [], "contradictions": []}
    )


# --- critique parsing (pure) ------------------------------------------------


def test_parse_followups_reads_gaps_and_specs():
    raw = """{
      "sufficient": false,
      "gaps": ["nothing on cost"],
      "subagents": [{"objective": "find cost data", "max_tool_calls": 99}]
    }"""
    out = engine.parse_followups(raw, max_subagents=4)
    assert out["gaps"] == ["nothing on cost"]
    assert out["subagents"][0]["objective"] == "find cost data"
    assert out["subagents"][0]["max_tool_calls"] == 25  # clamped
    assert out["subagents"][0]["name"] == "followup-1"


def test_parse_followups_specs_override_a_sufficient_claim():
    # A model that says "we're done" and then lists work to do found something it
    # wanted answered; the work wins.
    out = engine.parse_followups(
        '{"sufficient": true, "subagents": [{"objective": "o"}]}', max_subagents=4
    )
    assert out["sufficient"] is False


def test_parse_followups_drops_malformed_specs_without_failing():
    out = engine.parse_followups(
        '{"sufficient": false, "subagents": [{"no": "objective"}, "junk"]}',
        max_subagents=4,
    )
    assert out["subagents"] == []


def test_parse_followups_clamps_to_max_subagents():
    specs = ", ".join(f'{{"objective": "o{i}"}}' for i in range(10))
    out = engine.parse_followups(
        f'{{"sufficient": false, "subagents": [{specs}]}}', max_subagents=3
    )
    assert len(out["subagents"]) == 3


# --- tool-result summaries (pure) -------------------------------------------


def test_summarize_tool_result_shapes():
    assert "error" in engine.summarize_tool_result("web_search", {"error": "boom"})
    assert engine.summarize_tool_result("web_search", {"results": []}) == "no results"
    assert "2 result(s)" in engine.summarize_tool_result(
        "web_search", {"results": [{"title": "T"}, {"title": "U"}]}
    )
    assert "1 paper(s)" in engine.summarize_tool_result(
        "arxiv_search", {"entries": [{}]}
    )
    assert "chars" in engine.summarize_tool_result(
        "fetch_page", {"text": "abc", "title": "T"}
    )


# --- rounds and steering (driven) -------------------------------------------


def _choice() -> engine.ModelChoice:
    return engine.ModelChoice(P.provider_for("ollama"), "http://x", "m")


@pytest.fixture
def deep_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Like `fake_pipeline` but planned as `deep`, so rounds are allowed."""
    state: dict = {
        "critiques": [],
        "rounds_requested": 1,  # how many extra rounds the critique asks for
        "sleeps": [],
    }
    monkeypatch.setattr(engine, "resolve_models", lambda run: (_choice(), _choice()))

    async def fake_plan(run, lead):
        return (
            {
                "complexity": "deep",
                "subagents": [
                    {
                        "name": "s0",
                        "objective": "o0",
                        "output_format": "f",
                        "tool_guidance": "g",
                        "boundaries": "b",
                        "max_tool_calls": 3,
                    }
                ],
            },
            [],
            10,
        )

    async def fake_subagent(run, spec, sub, *, is_cancelled, on_tool=None):
        if on_tool is not None:
            on_tool(
                {
                    "seq": 1,
                    "name": "web_search",
                    "args": {"query": spec["objective"]},
                    "ok": True,
                    "ms": 1,
                    "summary": "1 result(s)",
                }
            )
        return (
            {
                "name": spec["name"],
                "findings": f"found by {spec['name']}",
                "sources": [
                    {"title": spec["name"], "url": f"https://{spec['name']}.com"}
                ],
                "tool_calls_used": 1,
            },
            [],
            20,
        )

    async def fake_critique(run, plan, outputs, lead, *, round_no, followups=None):
        state["critiques"].append(
            {"round": round_no, "followups": list(followups or [])}
        )
        if round_no < state["rounds_requested"]:
            return (
                {
                    "sufficient": False,
                    "gaps": ["gap"],
                    "subagents": [
                        {
                            "name": f"followup-{round_no}",
                            "objective": "close the gap",
                            "output_format": "f",
                            "tool_guidance": "g",
                            "boundaries": "b",
                            "max_tool_calls": 3,
                        }
                    ],
                },
                [],
                5,
            )
        return {"sufficient": True, "gaps": [], "subagents": []}, [], 5

    async def fake_synthesis(run, step_id, outputs, lead):
        numbered, _ = engine.number_sources(outputs)
        state["synthesis_inputs"] = [o["name"] for o in outputs]
        return {"report": "Report [1]", "sources": numbered}, [], 30

    async def fake_verification(run, synth, outputs, lead):
        return (
            {"skipped": False, "claims": [], "contradictions": [], "summary": {}},
            [],
            5,
        )

    async def fake_citations(run, synth, lead, verification=None):
        return (
            {
                "report": synth["report"] + "\n\n## References",
                "sources": synth["sources"],
                "stripped_markers": [],
            },
            [],
            15,
        )

    monkeypatch.setattr(engine, "run_plan_step", fake_plan)
    monkeypatch.setattr(engine, "run_subagent_step", fake_subagent)
    monkeypatch.setattr(engine, "run_critique_step", fake_critique)
    monkeypatch.setattr(engine, "run_synthesis_step", fake_synthesis)
    monkeypatch.setattr(engine, "run_verification_step", fake_verification)
    monkeypatch.setattr(engine, "run_citations_step", fake_citations)

    real_sleep = asyncio.sleep

    async def fast_sleep(seconds: float) -> None:
        state["sleeps"].append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(runner_mod.asyncio, "sleep", fast_sleep)
    return state


async def _drive(run_id: str, timeout: float = 10.0) -> dict:
    r = ResearchRunner()
    r.start()
    try:

        async def wait() -> dict:
            while True:
                run = runstore.get_run(run_id)
                assert run is not None
                if run["status"] in runstore.TERMINAL_STATUSES:
                    return run
                await asyncio.sleep(0)

        return await asyncio.wait_for(wait(), timeout)
    finally:
        r.stop()


def test_a_deep_run_spawns_a_second_round(deep_pipeline) -> None:
    run = runstore.create_run(query="deep one", effort="deep")
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "done"

    steps = runstore.list_steps(run["id"])
    subs = [s for s in steps if s["kind"] == "subagent"]
    assert {s["round"] for s in subs} == {0, 1}
    # One critique per completed round: round 0's asked for more, round 1's said
    # enough. Both are separate step rows — keying steps by kind alone would have
    # collapsed them and made round 1 look already-done.
    critiques = [s for s in steps if s["kind"] == "critique"]
    assert [s["round"] for s in critiques] == [0, 1]
    # Synthesis reads BOTH rounds — round 2 fills gaps, it doesn't replace round 1.
    assert set(deep_pipeline["synthesis_inputs"]) == {"s0", "followup-0"}
    assert final["rounds_used"] == 2


def test_a_sufficient_critique_stops_after_one_round(deep_pipeline) -> None:
    deep_pipeline["rounds_requested"] = 0
    run = runstore.create_run(query="enough already", effort="deep")
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "done"
    subs = [s for s in runstore.list_steps(run["id"]) if s["kind"] == "subagent"]
    assert {s["round"] for s in subs} == {0}
    assert final["rounds_used"] == 1


def test_rounds_are_capped_by_the_effort_tier(deep_pipeline, monkeypatch) -> None:
    # The critique always wants more, but `deep` tops out at three rounds.
    deep_pipeline["rounds_requested"] = 99
    run = runstore.create_run(query="endless", effort="deep")
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "done"
    subs = [s for s in runstore.list_steps(run["id"]) if s["kind"] == "subagent"]
    assert max(s["round"] for s in subs) == 2  # rounds 0,1,2 = the deep ceiling of 3


def test_tool_calls_are_persisted_per_step(deep_pipeline) -> None:
    deep_pipeline["rounds_requested"] = 0
    run = runstore.create_run(query="traced", effort="deep")
    asyncio.run(_drive(run["id"]))
    calls = runstore.list_tool_calls(run["id"])
    assert len(calls) == 1
    assert calls[0]["name"] == "web_search"
    assert calls[0]["ok"] is True
    assert calls[0]["args"]["query"] == "o0"


# --- the approval gate ------------------------------------------------------


def test_plan_mode_parks_the_run_and_releases_the_worker(deep_pipeline) -> None:
    run = runstore.create_run(query="ask me first", effort="deep", approval_mode="plan")
    run_id = run["id"]

    async def scenario() -> dict:
        r = ResearchRunner()
        r.start()
        try:

            async def wait() -> dict:
                for _ in range(2000):
                    current = runstore.get_run(run_id)
                    assert current is not None
                    if current["status"] == "awaiting_plan":
                        return current
                    await asyncio.sleep(0)
                raise AssertionError("run never parked")

            parked = await asyncio.wait_for(wait(), 5)

            # The worker is free: a second run started now must complete while the
            # first is still parked. This is the property that makes the gate a
            # return rather than a block.
            other = runstore.create_run(query="unblocked", effort="quick")
            r.enqueue(other["id"])

            async def wait_other() -> dict:
                while True:
                    current = runstore.get_run(other["id"])
                    if current["status"] in runstore.TERMINAL_STATUSES:
                        return current
                    await asyncio.sleep(0)

            second = await asyncio.wait_for(wait_other(), 5)
            assert second["status"] == "done"
            return parked
        finally:
            r.stop()

    parked = asyncio.run(scenario())
    assert parked["plan"]["subagents"][0]["name"] == "s0"
    # Still parked, and no subagent has spent anything.
    assert not [s for s in runstore.list_steps(run_id) if s["kind"] == "subagent"]


def test_a_parked_run_is_not_resumed_on_boot(deep_pipeline) -> None:
    # Otherwise every restart re-enqueues it and it spins through the gate forever.
    run = runstore.create_run(query="parked", effort="deep", approval_mode="plan")
    runstore.update_run(run["id"], status="awaiting_plan")
    resumable = {r["id"] for r in runstore.list_resumable_runs()}
    assert run["id"] not in resumable


def test_approving_an_edited_plan_resumes_the_run(deep_pipeline) -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    deep_pipeline["rounds_requested"] = 0
    run = runstore.create_run(query="edit me", effort="deep", approval_mode="plan")
    run_id = run["id"]

    async def park() -> None:
        r = ResearchRunner()
        r.start()
        try:
            for _ in range(2000):
                current = runstore.get_run(run_id)
                if current and current["status"] == "awaiting_plan":
                    return
                await asyncio.sleep(0)
            raise AssertionError("run never parked")
        finally:
            r.stop()

    asyncio.run(park())

    client = TestClient(app)
    edited = {
        "complexity": "quick",
        "subagents": [
            {
                "name": "my-own",
                "objective": "what I actually wanted",
                "output_format": "f",
                "tool_guidance": "g",
                "boundaries": "b",
                "max_tool_calls": 2,
            }
        ],
    }
    res = client.post(f"/api/research/runs/{run_id}/plan", json={"plan": edited})
    assert res.status_code == 200
    assert res.json()["approval_mode"] == "auto"

    # The edit must land on the plan STEP too — the pipeline reads it back from
    # there on resume, so a run-row-only write would be silently discarded.
    plan_step = next(s for s in runstore.list_steps(run_id) if s["kind"] == "plan")
    assert plan_step["output"]["subagents"][0]["name"] == "my-own"

    final = asyncio.run(_drive(run_id))
    assert final["status"] == "done"
    subs = [s for s in runstore.list_steps(run_id) if s["kind"] == "subagent"]
    assert [s["name"] for s in subs] == ["my-own"]


def test_approving_rejects_a_malformed_plan(deep_pipeline) -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    run = runstore.create_run(query="bad edit", effort="deep", approval_mode="plan")
    runstore.update_run(run["id"], status="awaiting_plan")
    client = TestClient(app)
    res = client.post(
        f"/api/research/runs/{run['id']}/plan", json={"plan": {"subagents": []}}
    )
    assert res.status_code == 400


def test_approving_a_running_run_is_a_conflict(deep_pipeline) -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    run = runstore.create_run(query="not parked", effort="deep")
    client = TestClient(app)
    res = client.post(f"/api/research/runs/{run['id']}/plan", json={})
    assert res.status_code == 409


# --- follow-ups -------------------------------------------------------------


def test_followups_reach_the_critique_and_are_consumed(deep_pipeline) -> None:
    run = runstore.create_run(query="steer me", effort="deep")
    runstore.add_followup(run["id"], "also check licensing")
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "done"

    seen = [f for c in deep_pipeline["critiques"] for f in c["followups"]]
    assert "also check licensing" in seen
    # Consumed, so the next round doesn't re-ask the same thing.
    assert not runstore.list_followups(run["id"], unconsumed_only=True)


def test_followup_route_rejects_a_finished_run(deep_pipeline) -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    deep_pipeline["rounds_requested"] = 0
    run = runstore.create_run(query="over", effort="deep")
    asyncio.run(_drive(run["id"]))
    client = TestClient(app)
    res = client.post(
        f"/api/research/runs/{run['id']}/followup", json={"text": "too late"}
    )
    assert res.status_code == 409
