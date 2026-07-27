"""The durable runner: retry/backoff, resume-on-restart, cancellation, budget
cutoff — the whole pipeline driven against a scripted fake engine (no model)."""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.agent import providers as P
from backend.modules.research import engine, runner as runner_mod, runstore
from backend.modules.research.runner import ResearchRunner


def _choice() -> engine.ModelChoice:
    return engine.ModelChoice(P.provider_for("ollama"), "http://x", "m")


@pytest.fixture
def fake_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Stub every engine step with fast fakes; returns a dict to tweak behavior."""
    state: dict = {
        "plan_failures": 0,
        "subagent_failures": {},
        "sleeps": [],
        "critiques": [],
        # What the critique returns by default: "we have enough", so a test that
        # doesn't care about rounds gets the old single-pass behaviour.
        "critique_result": {"sufficient": True, "gaps": [], "subagents": []},
    }

    monkeypatch.setattr(engine, "resolve_models", lambda run: (_choice(), _choice()))

    async def fake_plan(run, lead):
        if state["plan_failures"] > 0:
            state["plan_failures"] -= 1
            raise RuntimeError("planner hiccup")
        plan = {
            "complexity": "quick",
            "subagents": [
                {
                    "name": "s0",
                    "objective": "o0",
                    "output_format": "f",
                    "tool_guidance": "g",
                    "boundaries": "b",
                    "max_tool_calls": 3,
                },
                {
                    "name": "s1",
                    "objective": "o1",
                    "output_format": "f",
                    "tool_guidance": "g",
                    "boundaries": "b",
                    "max_tool_calls": 3,
                },
            ],
        }
        return plan, [{"role": "user", "content": "plan"}], 10

    async def fake_subagent(run, spec, sub, *, is_cancelled, on_tool=None):
        fails = state["subagent_failures"].get(spec["name"], 0)
        if fails > 0:
            state["subagent_failures"][spec["name"]] = fails - 1
            raise RuntimeError(f"{spec['name']} flaked")
        if on_tool is not None:
            on_tool(
                {
                    "seq": 1,
                    "name": "web_search",
                    "args": {"query": spec["objective"]},
                    "ok": True,
                    "ms": 5,
                    "summary": "2 result(s)",
                }
            )
        return (
            {
                "name": spec["name"],
                "findings": f"findings from {spec['name']}",
                "sources": [
                    {
                        "title": spec["name"],
                        "url": f"https://{spec['name']}",
                        "note": "",
                    }
                ],
                "tool_calls_used": 1,
            },
            [{"role": "user", "content": "sub"}],
            20,
        )

    async def fake_synthesis(run, step_id, outputs, lead):
        report = "Report body [1] [2]" if len(outputs) > 1 else "Report body [1]"
        numbered, _ = engine.number_sources(outputs)
        return {"report": report, "sources": numbered}, [], 30

    async def fake_critique(run, plan, outputs, lead, *, round_no, followups=None):
        state["critiques"].append(
            {"round": round_no, "followups": list(followups or [])}
        )
        return dict(state["critique_result"]), [], 5

    async def fake_verification(run, synth, outputs, lead):
        return (
            {
                "skipped": False,
                "claims": [],
                "contradictions": [],
                "summary": {"total": 0},
            },
            [],
            5,
        )

    async def fake_citations(run, synth, lead, verification=None):
        return (
            {
                "report": synth["report"] + "\n\n## References",
                "sources": synth["sources"],
                "stripped_markers": [],
                "verification": (verification or {}).get("summary") or {},
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

    # No real backoff waits in tests.
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds: float) -> None:
        state["sleeps"].append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(runner_mod.asyncio, "sleep", fast_sleep)
    return state


async def _drive(run_id: str, timeout: float = 10.0) -> dict:
    """Run a fresh runner until the given run reaches a terminal state."""
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


def test_full_pipeline_completes(fake_pipeline) -> None:
    run = runstore.create_run(query="what is up", effort="quick")
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "done"
    assert final["report_artifact_id"]
    assert final["report_source_id"]
    kinds = {s["kind"]: s["status"] for s in runstore.list_steps(run["id"])}
    assert kinds == {
        "plan": "done",
        "subagent": "done",  # dict collapses; check separately below
        "synthesis": "done",
        "verify": "done",
        "citations": "done",
        "export": "done",
    }
    # A quick run is one round, so no critique step is created at all — the round
    # cap is checked before spending a model call on "should we do more?".
    assert not [s for s in runstore.list_steps(run["id"]) if s["kind"] == "critique"]
    subs = [s for s in runstore.list_steps(run["id"]) if s["kind"] == "subagent"]
    assert len(subs) == 2 and all(s["status"] == "done" for s in subs)
    assert final["tokens_used"] > 0
    # The stored report artifact really contains the citation pass's output.
    from backend.modules.artifacts.store import artifact_path

    report = artifact_path(final["report_artifact_id"]).read_text(encoding="utf-8")
    assert "## References" in report


def test_step_retry_with_backoff(fake_pipeline) -> None:
    fake_pipeline["plan_failures"] = 2  # fails twice, succeeds on attempt 3
    run = runstore.create_run(query="retry me", effort="quick")
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "done"
    plan = next(s for s in runstore.list_steps(run["id"]) if s["kind"] == "plan")
    assert plan["attempt"] == 3
    # Exponential backoff slept before attempts 2 and 3: 5s then 10s.
    backoffs = [s for s in fake_pipeline["sleeps"] if s >= 5]
    assert backoffs[:2] == [5, 10]


def test_plan_exhaustion_fails_run(fake_pipeline) -> None:
    fake_pipeline["plan_failures"] = 99
    run = runstore.create_run(query="doomed", effort="quick")
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "failed"
    assert "attempts" in final["error"]


def test_failed_subagent_does_not_fail_run(fake_pipeline) -> None:
    fake_pipeline["subagent_failures"] = {"s1": 99}
    run = runstore.create_run(query="one flaky", effort="quick")
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "done"
    subs = {
        s["name"]: s["status"]
        for s in runstore.list_steps(run["id"])
        if s["kind"] == "subagent"
    }
    assert subs == {"s0": "done", "s1": "failed"}


def test_resume_after_crash(fake_pipeline) -> None:
    """Simulate a crash mid-run: plan done, one subagent left `running`, then a
    fresh runner boots — the stuck step resets and the run completes."""
    run = runstore.create_run(query="resume me", effort="quick")
    run_id = run["id"]
    # Hand-build the crashed state.
    plan = {
        "complexity": "quick",
        "subagents": [
            {
                "name": "s0",
                "objective": "o0",
                "output_format": "f",
                "tool_guidance": "g",
                "boundaries": "b",
                "max_tool_calls": 3,
            },
        ],
    }
    plan_step = runstore.create_step(run_id, seq=0, kind="plan", name="Plan the run")
    runstore.mark_step_running(plan_step["id"])
    runstore.finish_step(plan_step["id"], status="done", output=plan, tokens_used=10)
    runstore.update_run(run_id, plan=plan, status="researching")
    sub_step = runstore.create_step(
        run_id, seq=1, kind="subagent", name="s0", input=plan["subagents"][0]
    )
    runstore.mark_step_running(sub_step["id"])  # crashed while running (attempt=1)

    final = asyncio.run(_drive(run_id))
    assert final["status"] == "done"
    resumed = runstore.get_step(sub_step["id"])
    assert resumed["status"] == "done"
    assert resumed["attempt"] == 2  # the in-flight attempt + the resumed one
    # The plan step ran once — its checkpoint was reused, not recomputed.
    plan_after = runstore.get_step(plan_step["id"])
    assert plan_after["attempt"] == 1


def test_cancellation(fake_pipeline, monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging_subagent(run, spec, sub, *, is_cancelled, on_tool=None):
        started.set()
        await release.wait()
        raise runner_mod.RunCancelled

    monkeypatch.setattr(engine, "run_subagent_step", hanging_subagent)

    async def scenario() -> dict:
        run = runstore.create_run(query="cancel me", effort="quick")
        r = ResearchRunner()
        r.start()
        try:
            await asyncio.wait_for(started.wait(), 5)
            runstore.request_cancel(run["id"])
            release.set()

            async def wait() -> dict:
                while True:
                    current = runstore.get_run(run["id"])
                    if current["status"] in runstore.TERMINAL_STATUSES:
                        return current
                    await asyncio.sleep(0)

            return await asyncio.wait_for(wait(), 5)
        finally:
            r.stop()

    final = asyncio.run(scenario())
    assert final["status"] == "cancelled"


def test_budget_cutoff_skips_remaining_subagents(fake_pipeline) -> None:
    run = runstore.create_run(query="tiny budget", effort="quick", token_budget=1)
    # The plan step's 10 tokens blow the budget; both subagents must be skipped
    # → zero outputs → the run fails with a clear reason.
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "failed"
    assert "nothing to synthesize" in final["error"]
    subs = [s for s in runstore.list_steps(run["id"]) if s["kind"] == "subagent"]
    assert all(s["status"] == "skipped" for s in subs)


def test_retry_route_resets_failed_steps(fake_pipeline) -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    fake_pipeline["plan_failures"] = 99
    run = runstore.create_run(query="fail then retry", effort="quick")
    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "failed"

    fake_pipeline["plan_failures"] = 0
    client = TestClient(app)
    res = client.post(f"/api/research/runs/{run['id']}/retry")
    assert res.status_code == 200
    assert res.json()["status"] == "pending"
    plan = next(s for s in runstore.list_steps(run["id"]) if s["kind"] == "plan")
    assert plan["status"] == "pending" and plan["attempt"] == 0

    final = asyncio.run(_drive(run["id"]))
    assert final["status"] == "done"


def test_transcripts_persist_and_serve(fake_pipeline) -> None:
    from fastapi.testclient import TestClient

    from backend.app import app

    run = runstore.create_run(query="traced", effort="quick")
    asyncio.run(_drive(run["id"]))
    client = TestClient(app)
    res = client.get(
        f"/api/research/runs/{run['id']}/steps", params={"transcript": True}
    )
    assert res.status_code == 200
    steps = res.json()["steps"]
    plan = next(s for s in steps if s["kind"] == "plan")
    assert plan["transcript"] == [{"role": "user", "content": "plan"}]
    # Without the flag transcripts stay out of the payload.
    res = client.get(f"/api/research/runs/{run['id']}/steps")
    assert all(s.get("transcript") is None for s in res.json()["steps"])
