"""Live capture, driven through the **real** `run_agent_loop`.

The claim under test is the one the whole module rests on: hooking that single
function captures every internal source, and it does so without changing what the
loop does. So this reuses the evals runner's scripted-model harness — everything
between the script and the recorded trajectory is production code.
"""

from __future__ import annotations

import asyncio

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


@pytest.mark.anyio
async def test_prose_alongside_tool_calls_is_recorded(scripted, capture_on):
    """The model's stated reason for acting is part of the trajectory.

    A real model narrates and calls in the same turn. That prose used to be dropped
    on the floor — `message()` ran exactly twice per run, for the goal and the final
    answer — so a run read as a bare list of calls with no account of why any of them
    happened, which is the half a postmortem is actually looking for.
    """
    scripted(
        [
            {
                "content": "I'll open the terminal for you.",
                "calls": [("open_pane", {"id": "terminal"})],
            },
            "Opened the terminal.",
        ]
    )
    result = await _run(_case(), [tool_decl("ui.noop")])
    assert result.passed, result.detail

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    prose = [s for s in run.step_list if s.kind == "message" and s.role == "assistant"]
    assert [s.content for s in prose] == [
        "I'll open the terminal for you.",
        "Opened the terminal.",
    ]
    # The narration belongs to the round that acted, and it must come before the
    # action it explains — a reason recorded after its consequence is not a reason.
    assert prose[0].round == 0
    action = next(s for s in run.step_list if s.kind == "action")
    assert prose[0].seq < action.seq


@pytest.mark.anyio
async def test_a_final_answer_is_not_recorded_twice(scripted, capture_on):
    """The no-tool-call branch is `finish()`'s job alone.

    Recording prose in both places would duplicate every single-round answer, and a
    duplicated assistant message is invisible in the pane and poison in an SFT export.
    """
    scripted(["No tool needed."])
    await _run(
        _case(expect=Expect(grade="no_call"), fixtures={}), [tool_decl("ui.noop")]
    )

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    answers = [
        s for s in run.step_list if s.kind == "message" and s.role == "assistant"
    ]
    assert [s.content for s in answers] == ["No tool needed."]


@pytest.mark.anyio
async def test_an_action_timestamp_is_when_the_call_started(
    scripted, capture_on, monkeypatch
):
    """`ts` is the bar's left edge, so it must be the call's start, not its end.

    `rec.action` is invoked *after* the tool has been awaited, so letting the store
    default `ts` recorded the moment the call finished. Every bar on a timeline then
    sits one full duration to the right of where it belongs — a failure that draws a
    perfectly plausible chart, which is why it needs a test rather than an eyeball.

    The baseline is stamped *inside* the tool rather than before the turn: assembling
    a turn takes longer than this tool does, so measuring from before `_run` compares
    the wrong two numbers and fails whichever way `ts` is recorded.
    """
    import time

    from backend.modules.agent.offline_conn import OfflineConnection

    real = OfflineConnection.fixture_for
    entered: list[float] = []
    HELD = 0.2

    def slow(self, name):
        entered.append(time.time())
        time.sleep(HELD)
        return real(self, name)

    monkeypatch.setattr(OfflineConnection, "fixture_for", slow)

    scripted([[("open_pane", {"id": "terminal"})], "Opened the terminal."])
    await _run(_case(), [tool_decl("ui.noop")])

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    action = next(s for s in run.step_list if s.kind == "action")
    assert len(entered) == 1
    # Recorded at the call's start, not HELD seconds later at its end. The margin is
    # generous because it only has to separate "start" from "start + 200ms".
    assert abs(action.ts - entered[0]) < HELD / 2
    assert action.duration_ms >= HELD * 1000 * 0.75


@pytest.mark.anyio
async def test_provider_tokens_are_summed_onto_the_run(scripted, capture_on):
    """Token counts reach `traj_runs`, summed across the turn's rounds.

    They used to be NULL for every local run — `ChatResult` dropped the provider's
    usage block entirely, so the columns existed and nothing ever filled them.
    """
    from backend.modules.agent.providers import Usage

    scripted(
        [
            {
                "calls": [("open_pane", {"id": "terminal"})],
                "usage": Usage(tokens_in=100, tokens_out=20),
            },
            {
                "content": "Opened the terminal.",
                "usage": Usage(tokens_in=140, tokens_out=8),
            },
        ]
    )
    result = await _run(_case(), [tool_decl("ui.noop")])
    assert result.passed, result.detail

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    assert run.tokens_in == 240
    assert run.tokens_out == 28


@pytest.mark.anyio
async def test_a_local_run_costs_a_known_zero(scripted, capture_on):
    """`0.0`, not NULL. The pane renders this as `free`; NULL renders as nothing,
    which would be indistinguishable from a hosted model we have no price for."""
    from backend.modules.agent.providers import Usage

    scripted(
        [
            {
                "calls": [("open_pane", {"id": "terminal"})],
                "usage": Usage(tokens_in=10, tokens_out=2),
            },
            "Opened the terminal.",
        ]
    )
    await _run(_case(), [tool_decl("ui.noop")])

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    # The scripted harness runs as the `ollama` provider — see test_evals_runner.INFO.
    assert run.cost_usd == 0.0


@pytest.mark.anyio
async def test_a_provider_that_reports_nothing_leaves_the_totals_null(
    scripted, capture_on
):
    """Absent must not become zero on the way through. A run showing `0 tokens`
    reads as a measurement; NULL reads as "not reported", which is the truth."""
    scripted([[("open_pane", {"id": "terminal"})], "Opened the terminal."])
    await _run(_case(), [tool_decl("ui.noop")])

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    assert run.tokens_in is None
    assert run.tokens_out is None


@pytest.mark.anyio
async def test_the_forced_tool_retry_does_not_lose_its_first_rounds_tokens(
    scripted, capture_on
):
    """The bug a single end-of-round capture would have caused.

    When a model narrates an action without emitting the call, the loop re-asks and
    **reassigns** `result`. Counting once, at the end, would silently under-report
    exactly the turns that needed repairing — the ones already costing double.
    """
    from backend.modules.agent.providers import Usage

    scripted(
        [
            # Prose that trips `_looks_like_unemitted_tool_call`, with no call.
            {
                "content": "I'll open the terminal for you.",
                "usage": Usage(tokens_in=70, tokens_out=9),
            },
            # The retry, which does emit one.
            {
                "calls": [("open_pane", {"id": "terminal"})],
                "usage": Usage(tokens_in=85, tokens_out=11),
            },
            {
                "content": "Opened the terminal.",
                "usage": Usage(tokens_in=90, tokens_out=6),
            },
        ]
    )
    await _run(_case(), [tool_decl("ui.noop")])

    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    assert run.tokens_in == 70 + 85 + 90
    assert run.tokens_out == 9 + 11 + 6


@pytest.mark.anyio
async def test_a_turns_wire_traffic_is_persisted_against_its_run(scripted, capture_on):
    """The join this whole table exists for, end to end.

    The orchestrator stamps every request it makes with the turn (`telemetry/turn.py`),
    the drain keeps the stamped ones, and `traj_runs.turn_id` is what turns them back
    into "the I/O this run produced". Before this, the live ring held 500 events and
    a finished run's wire was simply gone.
    """
    from backend.modules.telemetry import drain, store as telemetry_store
    from backend.modules.telemetry.recorder import recorder as io_recorder

    telemetry_store._initialized.clear()
    telemetry_store.init_telemetry_db()
    drain.reset()
    io_recorder.clear()
    drain.start()
    # Let the task actually reach `recorder.subscribe()`. `create_task` only
    # schedules it, and an event recorded before it subscribes goes nowhere — the
    # same subscribe-before-you-produce ordering the trajectory client follows.
    await asyncio.sleep(0)

    scripted([[("open_pane", {"id": "terminal"})], "Opened the terminal."])
    await _run(_case(), [tool_decl("ui.noop")])

    # The scripted model replaces `chat_stream`, so no real HTTP happens. Record one
    # event inside the turn by hand: what is under test is the stamping and the
    # join, not httpx.
    run = capture_on.get_run(capture_on.list_runs()[0][0].id)
    io_recorder.record(
        source="outbound",
        method="POST",
        target="http://localhost:11434/api/chat",
        status=200,
        turn_id=run.turn_id,
        round=0,
    )
    await drain.stop()

    events = telemetry_store.for_turn(run.turn_id)
    assert [e["target"] for e in events] == ["http://localhost:11434/api/chat"]

    # And deleting the run takes it with it, rather than orphaning it.
    capture_on.delete_run(run.id)
    assert telemetry_store.for_turn(run.turn_id) == []
