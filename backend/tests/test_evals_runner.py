"""The in-node runner, driven by a scripted model.

The claim being tested is narrow and important: a case runs through the **real**
`run_agent_loop`, and `EvalConnection` is a complete stand-in for a browser. If
that were not true the harness would be measuring a tool-calling loop that does not
ship, and every number it produced would be about the harness.

The model is scripted rather than real for the obvious reason — a test that needs
Ollama running is a test that does not run — but everything between the script and
the recorded calls is production code: `_select_tools`, the permission gate,
`_call_frontend_tool`, `_dispatch_call`, the provider's message formatting.
"""

from __future__ import annotations

import pytest

from backend.modules.agent import providers as P
from backend.modules.evals import runner_agent
from backend.modules.evals.models import EvalCase, Expect, Expose, ToolCall

INFO = P.PROVIDERS["ollama"]


def tool_decl(name: str, description: str = "a tool") -> dict:
    """A frontend tool in the shape the browser pushes onto the connection."""
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        },
        "sideEffect": False,
    }


class ScriptedModel:
    """A `chat_stream` stand-in that plays a fixed list of turns.

    Each entry is either a string (a final answer) or a list of `(name, args)`
    pairs (tool calls). Running off the end returns a bland answer rather than
    raising, so a test that under-scripts fails on its assertion rather than on a
    StopIteration from inside the orchestrator.
    """

    def __init__(self, turns: list) -> None:
        self.turns = list(turns)
        self.seen: list[dict] = []

    async def __call__(
        self, client, info, endpoint, model, messages, tools, on_delta, **kw
    ):
        # Snapshot what the model was actually shown; several tests assert on it.
        self.seen.append({"messages": list(messages), "tools": list(tools)})
        turn = self.turns.pop(0) if self.turns else "done"
        if isinstance(turn, str):
            return P.ChatResult(
                assistant_message={"role": "assistant", "content": turn},
                tool_calls=[],
                content=turn,
            )
        calls = [
            P.ToolCall(id=f"c{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(turn)
        ]
        return P.ChatResult(
            assistant_message={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": c.name, "arguments": c.arguments}}
                    for c in calls
                ],
            },
            tool_calls=calls,
            content="",
        )


@pytest.fixture
def scripted(monkeypatch):
    def install(turns: list) -> ScriptedModel:
        model = ScriptedModel(turns)
        monkeypatch.setattr(P, "chat_stream", model)
        return model

    return install


async def run(case: EvalCase, tools: list[dict]):
    return await runner_agent.run_case(
        case,
        tools,
        provider=INFO,
        endpoint="http://localhost:11434",
        model="test-model",
    )


# --- the seam ---------------------------------------------------------------


@pytest.mark.anyio
async def test_a_tool_call_is_recorded_and_answered_from_the_fixture(scripted):
    """The whole seam in one test: the loop relays a call, `EvalConnection`
    resolves the future it registered, and the conversation continues past it."""
    scripted([[("open_pane", {"id": "terminal"})], "Opened the terminal."])
    case = EvalCase(
        id="open",
        prompt="open a terminal",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(
            grade="subset",
            calls=[ToolCall(name="open_pane", arguments={"id": "terminal"})],
        ),
        fixtures={"open_pane": {"opened": True}},
    )
    result = await run(case, [tool_decl("ui.noop")])

    assert result.passed, result.detail
    assert [c.name for c in result.actual] == ["open_pane"]
    assert result.answer == "Opened the terminal."
    assert not result.error


@pytest.mark.anyio
async def test_the_fixture_reaches_the_model_as_the_tool_result(scripted):
    """A multi-step case only works if the model can see what the tool returned."""
    model = scripted([[("list_open_panes", {})], "There are two panes."])
    case = EvalCase(
        id="list",
        prompt="what is open?",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(grade="name_only", calls=[ToolCall(name="list_open_panes")]),
        fixtures={"list_open_panes": {"panes": ["editor", "terminal"]}},
    )
    await run(case, [tool_decl("ui.noop")])

    # The second provider call is the one that saw the tool result.
    tool_messages = [m for m in model.seen[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages, "the tool result never reached the model"
    assert "terminal" in tool_messages[0]["content"]


@pytest.mark.anyio
async def test_a_tool_with_no_fixture_still_succeeds(scripted):
    """An unfixtured tool returns a bland success rather than an error: an error
    would make the model's next move a reaction to a broken tool instead of to the
    task, and the case would be measuring error handling."""
    model = scripted([[("whatever", {})], "ok"])
    case = EvalCase(
        id="nofixture",
        prompt="do it",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(grade="name_only", calls=[ToolCall(name="whatever")]),
    )
    result = await run(case, [tool_decl("ui.noop")])
    assert result.passed
    tool_messages = [m for m in model.seen[1]["messages"] if m.get("role") == "tool"]
    assert "error" not in tool_messages[0]["content"]


# --- what the model is shown ------------------------------------------------


@pytest.mark.anyio
async def test_explicit_exposure_shows_only_the_named_groups(scripted):
    model = scripted(["done"])
    case = EvalCase(
        id="scoped",
        prompt="hello",
        expose=Expose(mode="explicit", preload=["github"]),
        expect=Expect(grade="no_call"),
    )
    await run(case, [tool_decl("github.searchCode"), tool_decl("files.write")])

    names = {t["function"]["name"] for t in model.seen[0]["tools"]}
    assert "github.searchCode" in names
    assert "files.write" not in names


@pytest.mark.anyio
async def test_progressive_exposure_offers_the_meta_tools(scripted):
    """Progressive disclosure is the shipped path, so the default exposure has to
    put `list_tool_groups`/`load_tools` in front of the model — without them a
    model cannot reach any capability it was not preloaded with."""
    model = scripted(["done"])
    case = EvalCase(
        id="progressive",
        prompt="hello",
        expose=Expose(mode="progressive"),
        expect=Expect(grade="no_call"),
    )
    await run(case, [tool_decl("github.searchCode")])

    names = {t["function"]["name"] for t in model.seen[0]["tools"]}
    assert "load_tools" in names


@pytest.mark.anyio
async def test_the_case_supplies_the_workspace_context_not_the_machine(scripted):
    """A result must not depend on what happened to be open on the box that ran
    it, so context comes from the case."""
    model = scripted(["done"])
    case = EvalCase(
        id="ctx",
        prompt="hello",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(grade="no_call"),
        # The shape the frontend attaches to a real turn: a pane index, each
        # entry carrying the snapshot the pane published.
        context={
            "panes": [
                {
                    "title": "main.py",
                    "viewId": "editor.buffer",
                    "instanceId": "e1",
                    "snapshot": {"uri": "file:///main.py", "dirty": False},
                }
            ]
        },
    )
    await run(case, [tool_decl("ui.noop")])

    blob = " ".join(str(m.get("content", "")) for m in model.seen[0]["messages"])
    assert "main.py" in blob


# --- results ----------------------------------------------------------------


@pytest.mark.anyio
async def test_the_negative_case_catches_an_unwanted_call(scripted):
    scripted([[("open_pane", {"id": "terminal"})], "done"])
    case = EvalCase(
        id="chatty",
        prompt="how many panes do I have open?",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(grade="no_call"),
    )
    result = await run(case, [tool_decl("ui.noop")])
    assert not result.passed
    assert "open_pane" in result.detail


@pytest.mark.anyio
async def test_a_provider_error_is_a_failed_row_not_a_dead_sweep(scripted, monkeypatch):
    """One unreachable model must not abandon a sweep across five others — and
    "this model errored here" is itself a result worth keeping."""

    async def boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(P, "chat_stream", boom)
    case = EvalCase(
        id="broken",
        prompt="hello",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(grade="no_call"),
    )
    result = await run(case, [tool_decl("ui.noop")])

    assert not result.passed, "an errored case must not pass, even under no_call"
    assert "connection refused" in result.error


@pytest.mark.anyio
async def test_rounds_and_offered_tools_are_recorded(scripted):
    """A model that needed three rounds to reach a tool another found in one has
    not passed the same way, so the row carries the count."""
    scripted(
        [
            [("list_open_panes", {})],
            [("open_pane", {"id": "terminal"})],
            "done",
        ]
    )
    case = EvalCase(
        id="multi",
        prompt="open a terminal",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(
            grade="sequence",
            calls=[ToolCall(name="list_open_panes"), ToolCall(name="open_pane")],
        ),
    )
    result = await run(case, [tool_decl("ui.noop")])

    assert result.passed, result.detail
    assert result.rounds == 3
    assert result.tools_offered > 0
    assert result.duration_ms >= 0
