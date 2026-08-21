"""Live capture, driven through the **real** `run_agent_loop`.

The claim under test is the one the whole module rests on: hooking that single
function captures every internal source, and it does so without changing what the
loop does. So this reuses the evals runner's scripted-model harness — everything
between the script and the recorded trajectory is production code.
"""

from __future__ import annotations

import pytest

from backend.modules.evals import runner_agent
from backend.modules.evals.models import EvalCase, Expect, Expose, ToolCall
from backend.modules.trajectories import store
from backend.tests.test_evals_runner import INFO, ScriptedModel, tool_decl


@pytest.fixture()
def capture_on():
    """A node with capture switched on, pointed at one dataset."""
    store._initialized.clear()
    store.init_trajectories_db()
    store.create_dataset("cap", "Capture", capture=True)
    return store


@pytest.fixture()
def capture_off():
    store._initialized.clear()
    store.init_trajectories_db()
    store.create_dataset("cap", "Capture", capture=False)
    return store


@pytest.fixture
def scripted(monkeypatch):
    from backend.modules.agent import providers as P

    def install(turns: list) -> ScriptedModel:
        model = ScriptedModel(turns)
        monkeypatch.setattr(P, "chat_stream", model)
        return model

    return install


async def _run(case: EvalCase, tools: list[dict]):
    return await runner_agent.run_case(
        case,
        tools,
        provider=INFO,
        endpoint="http://localhost:11434",
        model="test-model",
    )


def _case(**over) -> EvalCase:
    body = dict(
        id="open",
        prompt="open a terminal",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(
            grade="subset",
            calls=[ToolCall(name="open_pane", arguments={"id": "terminal"})],
        ),
        fixtures={"open_pane": {"opened": True}},
    )
    body.update(over)
    return EvalCase(**body)


@pytest.mark.anyio
async def test_capture_off_records_nothing(scripted, capture_off):
    """Off is the default, and off must mean off."""
    scripted([[("open_pane", {"id": "terminal"})], "Opened the terminal."])
    result = await _run(_case(), [tool_decl("ui.noop")])
    assert result.passed, result.detail
    _, total = capture_off.list_runs()
    assert total == 0


@pytest.mark.anyio
async def test_a_turn_is_recorded_as_a_run(scripted, capture_on):
    scripted([[("open_pane", {"id": "terminal"})], "Opened the terminal."])
    result = await _run(_case(), [tool_decl("ui.noop")])
    assert result.passed, result.detail

    runs, total = capture_on.list_runs(dataset_id="cap")
    assert total == 1
    run = capture_on.get_run(runs[0].id)
    assert run.source == "local"
    assert run.status == "complete"
    assert run.goal == "open a terminal"
    assert run.model == "test-model"
    # The join into interpretability's `agent_turns` — the other half of the turn.
    assert run.turn_id
    assert run.harness and len(run.harness) == 16


@pytest.mark.anyio
async def test_the_action_carries_its_own_result(scripted, capture_on):
    """A tool call and its result are one step. This is the convention the whole
    schema is built on, so it gets an explicit test."""
    scripted([[("open_pane", {"id": "terminal"})], "Opened the terminal."])
    await _run(_case(), [tool_decl("ui.noop")])

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    actions = [s for s in run.step_list if s.kind == "action"]
    assert len(actions) == 1
    assert actions[0].name == "open_pane"
    assert actions[0].args == {"id": "terminal"}
    assert actions[0].result == {"opened": True}
    assert actions[0].ok is True
    assert actions[0].duration_ms is not None


@pytest.mark.anyio
async def test_the_same_tool_twice_in_one_round_stays_two_steps(scripted, capture_on):
    """The failure that pairing-by-name would cause, pinned."""
    scripted(
        [
            [("open_pane", {"id": "a"}), ("open_pane", {"id": "b"})],
            "Opened both.",
        ]
    )
    await _run(
        _case(expect=Expect(grade="name_only", calls=[ToolCall(name="open_pane")])),
        [tool_decl("ui.noop")],
    )

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    actions = [s for s in run.step_list if s.kind == "action"]
    assert [a.args["id"] for a in actions] == ["a", "b"]


@pytest.mark.anyio
async def test_the_final_answer_is_recorded(scripted, capture_on):
    scripted([[("open_pane", {"id": "terminal"})], "Opened the terminal."])
    await _run(_case(), [tool_decl("ui.noop")])

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    answers = [
        s for s in run.step_list if s.kind == "message" and s.role == "assistant"
    ]
    assert answers[-1].content == "Opened the terminal."
    # And the prompt, so the run reads as a conversation.
    assert run.step_list[0].role == "user"


@pytest.mark.anyio
async def test_a_run_with_no_tool_call_is_still_recorded(scripted, capture_on):
    """ "The agent answered instead of acting" is the finding, not the absence of
    one — so it has to be in the data."""
    scripted(["I don't think I need a tool for that."])
    await _run(
        _case(expect=Expect(grade="no_call"), fixtures={}), [tool_decl("ui.noop")]
    )

    runs, total = capture_on.list_runs()
    assert total == 1
    run = capture_on.get_run(runs[0].id)
    assert run.status == "complete"
    assert not [s for s in run.step_list if s.kind == "action"]


@pytest.mark.anyio
async def test_a_provider_failure_seals_the_run_as_failed(
    scripted, capture_on, monkeypatch
):
    """A run left `running` forever is worse than no run: it is a lie the pane
    renders as "still going"."""
    from backend.modules.agent import providers as P

    async def boom(*a, **kw):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(P, "chat_stream", boom)
    await _run(_case(), [tool_decl("ui.noop")])

    runs, total = capture_on.list_runs()
    assert total == 1
    run = capture_on.get_run(runs[0].id)
    assert run.status == "failed"
    assert "provider exploded" in run.error


@pytest.mark.anyio
async def test_capture_failure_never_breaks_the_turn(scripted, capture_on, monkeypatch):
    """The rule the whole recorder is written around."""
    from backend.modules.trajectories import recorder as traj_recorder

    def explode(*a, **kw):
        raise RuntimeError("store is on fire")

    monkeypatch.setattr(traj_recorder.store, "append_step", explode)
    scripted([[("open_pane", {"id": "terminal"})], "Opened the terminal."])
    result = await _run(_case(), [tool_decl("ui.noop")])

    assert result.passed, result.detail
    assert result.answer == "Opened the terminal."


@pytest.mark.anyio
async def test_two_prompts_share_a_harness_but_not_a_run(scripted, capture_on):
    """The property the compare view needs: same configuration, one fingerprint."""
    scripted([[("open_pane", {"id": "terminal"})], "one"])
    await _run(_case(id="a"), [tool_decl("ui.noop")])
    scripted([[("open_pane", {"id": "terminal"})], "two"])
    await _run(_case(id="b", prompt="do something else"), [tool_decl("ui.noop")])

    runs, total = capture_on.list_runs()
    assert total == 2
    assert runs[0].harness == runs[1].harness
    assert len(capture_on.list_harnesses()) == 1
