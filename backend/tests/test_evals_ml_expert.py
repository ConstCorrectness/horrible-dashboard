"""The bundled ML Expert suite, and sweeping a suite as a roster agent.

The suite is data, so what can rot is its agreement with the agent it grades: a
case expecting a tool the agent is never offered fails every model, and the
scoreboard blames the model."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.agent.orchestrator import _group_of
from backend.modules.agent.roster import get_agent
from backend.modules.evals import bundled, runner_agent, store, sweep
from backend.modules.evals.models import CaseResult, RunTarget
from backend.sdk.registry import registry

SUITE = "bundled:ml-expert"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store._initialized.clear()
    return TestClient(app)


def _cases():
    suite = bundled.get_bundled(SUITE)
    assert suite is not None
    return store.load_cases(suite)


def test_suite_is_listed_after_the_starter(client):
    suites = client.get("/api/evals/suites").json()["suites"]
    ids = [s["id"] for s in suites if s["source"] == "bundled"]
    # Several tests take "the first bundled suite" to mean the starter.
    assert ids[:2] == ["bundled:starter", SUITE]


def test_every_tool_the_suite_names_is_offered_to_the_ml_expert(client):
    spec = get_agent("ml_expert")
    assert spec is not None
    registered = {t["function"]["name"] for t in registry.provider_tools(grouped=True)}
    for case in _cases():
        named = {c.name for c in case.expect.calls} | set(case.fixtures)
        for name in named:
            assert name in registered, f"{case.id}: {name} is not a registered tool"
            assert _group_of(name) in spec.tool_groups, (
                f"{case.id}: {name} is outside the ML Expert's groups"
            )
        for group in case.expose.preload:
            assert group in spec.tool_groups, f"{case.id}: preloads {group}"


def test_suite_has_negatives_and_judged_answers():
    grades = [c.expect.grade for c in _cases()]
    assert grades.count("no_call") >= 3
    assert grades.count("judge") >= 3


def _target(**extra) -> RunTarget:
    return RunTarget(
        provider="ollama", endpoint="http://localhost:11434", model="m", **extra
    )


def _recorder(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def fake_run_case(case, agent_tools, **kwargs):
        seen.append(kwargs["agent_id"])
        return CaseResult(case_id=case.id, passed=True, grade=case.expect.grade)

    monkeypatch.setattr(runner_agent, "run_case", fake_run_case)
    return seen


def test_the_suite_declares_its_agent(client):
    suites = client.get("/api/evals/suites").json()["suites"]
    by_id = {s["id"]: s for s in suites}
    assert by_id[SUITE]["agent_id"] == "ml_expert"
    assert by_id["bundled:starter"]["agent_id"] == ""


@pytest.mark.anyio
async def test_a_target_without_an_agent_answers_as_the_suites(client, monkeypatch):
    seen = _recorder(monkeypatch)
    runs = await sweep.run_sweep(SUITE, [_target()], [], case_ids=["mlx-neg-thanks"])
    assert runs[0].status == "done"
    assert seen == ["ml_expert"]
    # The same model as two agents must not share a scoreboard column.
    assert runs[0].label == "m · ml_expert"


@pytest.mark.anyio
async def test_the_targets_agent_wins_over_the_suites(client, monkeypatch):
    seen = _recorder(monkeypatch)
    runs = await sweep.run_sweep(
        SUITE, [_target(agent_id="main")], [], case_ids=["mlx-neg-thanks"]
    )
    assert seen == ["main"]
    assert runs[0].label == "m"


@pytest.mark.anyio
async def test_a_suite_without_an_agent_answers_as_main(client, monkeypatch):
    seen = _recorder(monkeypatch)
    await sweep.run_sweep(
        "bundled:starter", [_target()], [], case_ids=["negative-general-knowledge"]
    )
    assert seen == ["main"]


@pytest.mark.anyio
async def test_an_unknown_agent_fails_the_run_not_every_case(client, monkeypatch):
    async def never(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("ran a case for an agent that does not exist")

    monkeypatch.setattr(runner_agent, "run_case", never)
    runs = await sweep.run_sweep(SUITE, [_target(agent_id="ml_expret")], [])
    assert runs[0].status == "failed"
    assert "ml_expret" in (runs[0].error or "")


@pytest.mark.anyio
async def test_a_crashed_sweep_closes_its_runs_as_failed(client, monkeypatch):
    """A row left `running` reads as a sweep still in progress, forever."""

    async def crash(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(runner_agent, "run_case", crash)
    key = sweep.start_sweep(SUITE, [_target()], [], case_ids=["mlx-neg-thanks"])
    await sweep._running[key]
    run = store.list_runs(SUITE)[0]
    assert run.status == "failed"
    assert "kaboom" in (run.error or "")


def test_the_node_target_pairs_the_agents_model_with_its_provider(client, monkeypatch):
    """The provider came from the agent's settings and the model from the saved
    global config, so a NIM-configured agent was offered "a local GGUF on NIM"."""
    from backend.modules.agent import orchestrator, routes

    class _Config:
        provider = "lmstudio"
        endpoint = "http://localhost:1234"
        model = "global-model"

    monkeypatch.setattr(routes, "_load_config", lambda: _Config())
    monkeypatch.setattr(
        orchestrator,
        "_agent_setting",
        lambda _agent, key, default=None: "agent-model" if key == "model" else default,
    )
    targets = client.get("/api/evals/targets").json()["targets"]
    node = next(t for t in targets if t["source"] == "agent")
    assert node["model"] == "agent-model"
