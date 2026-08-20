"""The SFT exporter: what it writes, and what it refuses to invent.

The two claims worth pinning are that the exported trajectory is the **ideal** one
rather than the model's, and that the two chat-template traps are handled as code
rather than as advice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.evals import export, store
from backend.modules.evals.models import CaseResult, EvalCase, Expect, Expose, ToolCall


@pytest.fixture
def node(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store._initialized.clear()
    return TestClient(app)


def make_suite(cases: list[EvalCase]) -> str:
    suite = store.create_suite("Export fixture")
    store.write_cases(suite, cases)
    return suite.id


def make_run(suite_id: str, results: list[CaseResult], label: str = "m") -> str:
    run = store.create_run(suite_id, label, "ollama", "http://x", label, len(results))
    for r in results:
        store.save_result(run.id, r)
    return run.id


def tool_case(case_id: str = "open") -> EvalCase:
    return EvalCase(
        id=case_id,
        prompt="open a terminal",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(
            grade="subset",
            calls=[ToolCall(name="show", arguments={"target": "terminal"})],
        ),
        fixtures={"show": {"opened": True}},
    )


def answer_case(case_id: str = "greet") -> EvalCase:
    return EvalCase(
        id=case_id,
        prompt="hey, are you there?",
        expect=Expect(grade="no_call"),
    )


def result(case_id: str, passed: bool, answer: str = "", error: str = "") -> CaseResult:
    return CaseResult(
        case_id=case_id,
        passed=passed,
        grade="subset",
        detail="",
        answer=answer,
        error=error,
    )


def read(path: str) -> list[dict]:
    return [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
    ]


# --- the ideal trajectory ----------------------------------------------------


def test_a_failed_case_exports_the_expected_call_not_the_models_mistake(node):
    """The whole point of `repair`: what you train on is what the case says is
    correct, never the trajectory you are trying to remove."""
    suite = make_suite([tool_case()])
    # The model called the wrong thing and failed.
    run = make_run(suite, [result("open", passed=False)])

    out = export.build(run, mode="repair")
    assert out.repaired == 1 and out.correct == 0

    example = read(out.path)[0]
    assistant = next(m for m in example["messages"] if m["role"] == "assistant")
    call = assistant["tool_calls"][0]["function"]
    assert call["name"] == "show"
    assert json.loads(call["arguments"]) == {"target": "terminal"}


def test_the_trajectory_includes_the_tool_result(node):
    """An example that stops at the call teaches a model to call and never to use
    what came back — which is what a model looping on one tool looks like."""
    suite = make_suite([tool_case()])
    run = make_run(suite, [result("open", passed=False)])

    example = read(export.build(run, mode="repair").path)[0]
    tool_msg = next(m for m in example["messages"] if m["role"] == "tool")
    assert json.loads(tool_msg["content"]) == {"opened": True}


def test_modes_select_different_cases(node):
    suite = make_suite([tool_case("a"), tool_case("b")])
    run = make_run(suite, [result("a", passed=True), result("b", passed=False)])

    assert export.build(run, mode="correct").examples == 1
    assert export.build(run, mode="repair").examples == 1
    assert export.build(run, mode="both").examples == 2


# --- the two traps, as code --------------------------------------------------


def test_there_is_exactly_one_system_message(node):
    """A strict chat template raises on a second one — the failure is a 500 from
    the engine, not a warning."""
    suite = make_suite([tool_case()])
    run = make_run(suite, [result("open", passed=False)])

    for example in read(export.build(run, mode="repair").path):
        systems = [m for m in example["messages"] if m["role"] == "system"]
        assert len(systems) == 1
        assert example["messages"][0]["role"] == "system"


def test_examples_carry_tools_and_are_not_pre_rendered(node):
    """Rendering here would bake in one model's template and silently mistrain any
    other. The tokenizer's own `apply_chat_template(messages, tools=...)` is what
    matches the serving path."""
    suite = make_suite([tool_case()])
    run = make_run(suite, [result("open", passed=False)])

    example = read(export.build(run, mode="repair").path)[0]
    assert isinstance(example["messages"], list)
    assert "tools" in example
    assert "text" not in example and "prompt" not in example


# --- what it refuses to invent ----------------------------------------------


def test_an_answer_case_without_a_reference_is_skipped_not_fabricated(node):
    """A `no_call` case expects prose the case does not contain. Inventing it
    would be training data nobody checked."""
    suite = make_suite([answer_case()])
    run = make_run(suite, [result("greet", passed=False)])

    out = export.build(run, mode="repair")
    assert out.examples == 0
    assert any("reference_run_id" in s for s in out.skipped)


def test_a_reference_run_supplies_the_answer(node):
    """The real workflow: a strong model answers, a small one is trained on it."""
    suite = make_suite([answer_case()])
    weak = make_run(suite, [result("greet", passed=False)], label="weak")
    strong = make_run(
        suite, [result("greet", passed=True, answer="Yes, I am here.")], label="strong"
    )

    out = export.build(weak, mode="repair", reference_run_id=strong)
    assert out.examples == 1
    example = read(out.path)[0]
    assistant = next(m for m in example["messages"] if m["role"] == "assistant")
    assert assistant["content"] == "Yes, I am here."
    assert "tool_calls" not in assistant


def test_this_runs_own_correct_answers_are_reused(node):
    """A case this model already answers correctly needs no stronger model."""
    suite = make_suite([answer_case()])
    run = make_run(suite, [result("greet", passed=True, answer="Yes.")])

    out = export.build(run, mode="correct")
    assert out.examples == 1


def test_an_errored_case_is_skipped(node):
    """A provider timeout is not a lesson."""
    suite = make_suite([tool_case()])
    run = make_run(suite, [result("open", passed=False, error="connection refused")])

    out = export.build(run, mode="repair")
    assert out.examples == 0
    assert any("errored" in s for s in out.skipped)


def test_every_skip_says_which_case_and_why(node):
    """An exporter that quietly drops a third of a suite produces a dataset whose
    coverage nobody can account for."""
    suite = make_suite([answer_case("a"), tool_case("b")])
    run = make_run(
        suite, [result("a", passed=False), result("b", passed=False, error="boom")]
    )

    out = export.build(run, mode="repair")
    assert len(out.skipped) == 2
    for note in out.skipped:
        assert ":" in note and note.split(":")[0] in {"a", "b"}


# --- writing ------------------------------------------------------------------


def test_a_relative_out_path_cannot_escape_the_exports_directory(node):
    """An export name arrives in an HTTP body, and `joinpath` would happily follow
    `../..`."""
    suite = make_suite([tool_case()])
    run = make_run(suite, [result("open", passed=False)])

    with pytest.raises(ValueError, match="outside the exports directory"):
        export.build(run, mode="repair", out="../../escaped.jsonl")


def test_the_default_path_names_the_run(node):
    suite = make_suite([tool_case()])
    run = make_run(suite, [result("open", passed=False)], label="llama-3b")

    out = export.build(run, mode="repair")
    assert "llama-3b" in out.path and out.path.endswith(".jsonl")
    assert Path(out.path).exists()


def test_preview_writes_nothing_permanent(node):
    suite = make_suite([tool_case()])
    run = make_run(suite, [result("open", passed=False)])

    examples = export.preview(run, limit=2, mode="repair")
    assert len(examples) == 1
    assert not list(export.exports_dir().glob("*.jsonl"))


def test_the_route_exports_over_http(node):
    suite = make_suite([tool_case()])
    run = make_run(suite, [result("open", passed=False)])

    body = node.post(
        "/api/evals/exports", json={"run_id": run, "mode": "repair"}
    ).json()
    assert body["examples"] == 1
    assert body["repaired"] == 1


def test_an_unknown_run_is_a_422_not_a_500(node):
    assert node.post("/api/evals/exports", json={"run_id": "nope"}).status_code == 422
