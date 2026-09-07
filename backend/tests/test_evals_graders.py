"""Grading, which is the part of an eval harness people argue about.

Kept settleable by reading a table rather than by running a model: every case here
is pure input and expected verdict.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.modules.evals.graders import grade_case
from backend.modules.evals.models import EvalCase, Expect, ToolCall


def case(grade: str, calls: list[ToolCall] | None = None, **kw) -> EvalCase:
    return EvalCase(
        id="c",
        prompt="p",
        expect=Expect(grade=grade, calls=calls or [], **kw),
    )


def call(name: str, **args) -> ToolCall:
    return ToolCall(name=name, arguments=args)


# --- no_call: the negative case ---------------------------------------------


def test_no_call_passes_when_the_model_just_answered():
    passed, detail = grade_case(case("no_call"), [], "Yes, three panes are open.")
    assert passed
    assert "without calling" in detail


def test_no_call_fails_and_names_what_was_called():
    """The failure a suite exists to catch: a model that reaches for a tool when
    the question wanted an answer."""
    passed, detail = grade_case(case("no_call"), [call("open_pane", id="terminal")], "")
    assert not passed
    assert "open_pane" in detail


# --- subset: the default ----------------------------------------------------


def test_subset_tolerates_extra_arguments():
    expected = [call("open_pane", id="terminal")]
    actual = [call("open_pane", id="terminal", position="right")]
    passed, _ = grade_case(case("subset", expected), actual, "")
    assert passed


def test_subset_rejects_a_wrong_argument_value():
    expected = [call("open_pane", id="terminal")]
    actual = [call("open_pane", id="editor")]
    passed, detail = grade_case(case("subset", expected), actual, "")
    assert not passed
    # The near miss is named: "wrong id" and "never called it" are different
    # problems and the detail line has to distinguish them.
    assert "open_pane" in detail and "but expected" in detail


def test_a_missing_call_is_reported_differently_from_a_wrong_one():
    expected = [call("open_pane", id="terminal")]
    passed, detail = grade_case(case("subset", expected), [call("close_pane")], "")
    assert not passed
    assert "called nothing" not in detail
    assert "expected" in detail

    passed, detail = grade_case(case("subset", expected), [], "")
    assert not passed
    assert "nothing" in detail


def test_extra_calls_are_surfaced_even_on_a_pass():
    """A model that opened the right pane *and* deleted a file has not really
    passed; the row should let the reader decide that."""
    expected = [call("open_pane", id="terminal")]
    actual = [call("open_pane", id="terminal"), call("files.delete", path="x")]
    passed, detail = grade_case(case("subset", expected), actual, "")
    assert passed
    assert "plus" in detail and "files.delete" in detail


# --- exact vs name_only -----------------------------------------------------


def test_exact_rejects_an_extra_argument_subset_would_allow():
    expected = [call("open_pane", id="terminal")]
    actual = [call("open_pane", id="terminal", position="right")]
    assert grade_case(case("subset", expected), actual, "")[0]
    assert not grade_case(case("exact", expected), actual, "")[0]


def test_name_only_ignores_arguments_entirely():
    expected = [call("open_pane", id="terminal")]
    actual = [call("open_pane", id="something-else")]
    assert grade_case(case("name_only", expected), actual, "")[0]
    assert not grade_case(case("subset", expected), actual, "")[0]


# --- argument normalisation -------------------------------------------------


@pytest.mark.parametrize(
    "want,got",
    [
        ("terminal", " Terminal "),  # whitespace and case
        (1, 1.0),  # int vs float
        (1, "1"),  # a schema-typed number answered as a string
        (True, True),
    ],
)
def test_values_that_mean_the_same_thing_match(want, got):
    """A model that formatted an argument differently picked the right tool and the
    right argument; being strict here measures JSON formatting, not tool use."""
    passed, _ = grade_case(case("subset", [call("t", v=want)]), [call("t", v=got)], "")
    assert passed


def test_true_is_not_one():
    """`bool` is an `int` in Python and `True == 1`. Normalising booleans through
    the number branch would make `verbose=true` match `verbose=1`, which is a
    different argument."""
    passed, _ = grade_case(case("subset", [call("t", v=True)]), [call("t", v=1)], "")
    assert not passed


def test_null_is_not_the_same_as_absent():
    """ "Passed null explicitly" and "did not pass it" are different choices."""
    passed, _ = grade_case(case("subset", [call("t", v=None)]), [call("t")], "")
    assert not passed


# --- sequence ---------------------------------------------------------------


def test_sequence_allows_extra_calls_in_between():
    """A subsequence, not an exact list: a model that looked something up before
    acting was careful, not wrong."""
    expected = [call("list_open_panes"), call("open_pane", id="terminal")]
    actual = [
        call("list_open_panes"),
        call("get_pane_context", id="editor"),
        call("open_pane", id="terminal"),
    ]
    assert grade_case(case("sequence", expected), actual, "")[0]


def test_sequence_rejects_the_wrong_order():
    expected = [call("list_open_panes"), call("open_pane", id="terminal")]
    actual = [call("open_pane", id="terminal"), call("list_open_panes")]
    passed, detail = grade_case(case("sequence", expected), actual, "")
    assert not passed
    assert "in order" not in detail


# --- the two ways a case can be malformed -----------------------------------


def test_a_case_expecting_nothing_is_a_broken_case_not_a_pass():
    """Declaring no expected calls under a positive grade is a mistake — and it
    would otherwise pass trivially against every model forever."""
    passed, detail = grade_case(case("subset", []), [], "")
    assert not passed
    assert "no_call" in detail


def test_judge_grading_is_authorable_now_that_something_routes_it():
    """`judge` was rejected while it was declared-but-unrouted, because a grade
    nothing scores fails every run and the failure reads as the *model* getting it
    wrong. `evals/judge.py` routes it, so the case is now legal — the rule was
    never "judge is bad", it was "a grade nothing scores is".
    """
    assert case("judge", [], rubric="is it polite").expect.grade == "judge"


def test_a_judge_case_without_a_rubric_is_refused():
    """A judge with no rubric is a model asked to grade nothing in particular.
    Caught while authoring, where the mistake costs nothing, rather than twenty
    minutes into a sweep."""
    with pytest.raises(ValidationError):
        case("judge", [], rubric="")


def test_the_grader_still_refuses_judge_if_one_reaches_it():
    """The validator closes the authoring paths; this is the backstop under it.

    A suite file written before the validator existed still parses through the same
    model — but a row could reach the grader by some route nobody has thought of,
    and scoring it silently is the failure mode worth keeping a branch for.
    """
    unchecked = EvalCase(
        id="c",
        prompt="p",
        expect=Expect.model_construct(grade="judge", calls=[], rubric="is it polite"),
    )
    passed, detail = grade_case(unchecked, [], "hi")
    assert not passed
    assert "judge" in detail


def test_no_call_fails_on_an_empty_reply():
    """Saying nothing is not answering.

    A regression test for a real incident: a sweep where every case came back empty
    scored 6/13, because every `no_call` case counted the silence as a correct
    refusal to act. The actual cause was a provider error swallowed mid-stream, and
    this grade was what hid it.
    """
    passed, detail = grade_case(case("no_call"), [], "")
    assert not passed
    assert "nothing at all" in detail

    # Whitespace is not an answer either.
    assert not grade_case(case("no_call"), [], "   \n ")[0]


def test_no_call_still_passes_on_a_real_answer():
    passed, _ = grade_case(case("no_call"), [], "GGUF is a file format for models.")
    assert passed


# --- code execution -----------------------------------------------------------
#
# Two correct solutions to one problem share almost no characters, so every string
# metric scores code at zero. That is why HumanEval had no preset: not the dataset,
# the scorer.


def test_code_execution_is_off_unless_explicitly_enabled(monkeypatch):
    """It runs model-written code on this machine. Isolated, not container-grade —
    so it is a decision, never a default."""
    from backend.modules.evals import code_exec

    monkeypatch.delenv(code_exec.ENV_FLAG, raising=False)
    assert code_exec.enabled() is False
    with pytest.raises(code_exec.CodeExecError, match=code_exec.ENV_FLAG):
        code_exec.run_case(prompt="", completion="x = 1", test="", entry_point="")

    for value in ("1", "true", "yes"):
        monkeypatch.setenv(code_exec.ENV_FLAG, value)
        assert code_exec.enabled() is True
    for value in ("", "0", "false", "no"):
        monkeypatch.setenv(code_exec.ENV_FLAG, value)
        assert code_exec.enabled() is False


def test_a_fenced_reply_is_unwrapped_before_it_is_run():
    """Models fence their code. Running the fence is a SyntaxError scored as a
    wrong answer."""
    from backend.modules.evals.code_exec import extract_code

    assert extract_code("Sure!\n```python\ndef f():\n    return 1\n```\nDone.") == (
        "def f():\n    return 1"
    )
    # The longest block wins: a model that shows a wrong attempt then the real one
    # usually writes more in the second.
    assert "return 2" in extract_code("```\nx\n```\ntext\n```\ndef f():\n    return 2\n```")
    assert extract_code("def f(): return 1") == "def f(): return 1"


def test_a_body_only_completion_gets_its_stub_back():
    """HumanEval's format is a COMPLETION: the model is given a signature and
    returns a body. Running the body alone is a NameError."""
    from backend.modules.evals.code_exec import build_program

    program = build_program("def sq(x):", "    return x * x", "assert sq(3) == 9", "sq")
    assert "def sq(x):" in program
    compile(program, "<t>", "exec")

    # A completion that already defines the function must NOT get a second stub.
    whole = build_program("def sq(x):", "def sq(x):\n    return x * x", "assert sq(3) == 9", "sq")
    assert whole.count("def sq(") == 1


@pytest.mark.parametrize(
    ("completion", "expected"),
    [
        ("def sq(x):\n    return x * x", True),
        ("def sq(x):\n    return x + 1", False),
        ("def sq(x):\n    while True:\n        pass", False),
        ("this is not python", False),
    ],
)
def test_code_is_graded_by_running_the_datasets_tests(monkeypatch, completion, expected):
    from backend.modules.evals import code_exec

    monkeypatch.setenv(code_exec.ENV_FLAG, "1")
    passed, detail = code_exec.run_case(
        prompt="def sq(x):",
        completion=completion,
        test="def check(f):\n    assert f(3) == 9\n    assert f(4) == 16",
        entry_point="sq",
        timeout=6,
    )
    assert passed is expected
    assert detail  # never silent, pass or fail


def test_a_timeout_is_a_wrong_answer_not_an_error(monkeypatch):
    """An infinite loop IS a wrong answer to a programming problem. Calling it
    infrastructure trouble would hide a real result."""
    from backend.modules.evals import code_exec

    monkeypatch.setenv(code_exec.ENV_FLAG, "1")
    passed, detail = code_exec.run_case(
        prompt="def f():",
        completion="def f():\n    while True:\n        pass",
        test="def check(g):\n    g()",
        entry_point="f",
        timeout=2,
    )
    assert passed is False
    assert "timed out" in detail


def test_a_code_case_is_refused_at_job_time_when_execution_is_off(monkeypatch):
    """Refused where it is cheap. Generating the job and letting every row fail
    would score a zero that reads as a verdict on the model."""
    from backend.modules.evals import code_exec, runner_project
    from backend.modules.evals.models import EvalCase, HfBenchmark

    monkeypatch.delenv(code_exec.ENV_FLAG, raising=False)
    bench = HfBenchmark(dataset="openai/openai_humaneval", metric="code_exec")
    code_case = EvalCase(id="he", type="hf_benchmark", prompt="", benchmark=bench)
    with pytest.raises(ValueError, match=code_exec.ENV_FLAG):
        runner_project._job_for(code_case, "http://x", "m")

    monkeypatch.setenv(code_exec.ENV_FLAG, "1")
    job = runner_project._job_for(code_case, "http://x", "m")
    # The harness needs both columns to find the tests and the function under test.
    assert job["test_column"] == "test"
    assert job["entry_point_column"] == "entry_point"


def test_code_exec_needs_no_extra_package():
    """`evaluate` pulls a large tree; running the dataset's own tests needs
    nothing but permission."""
    from backend.modules.evals import runner_project
    from backend.modules.evals.models import EvalCase, HfBenchmark

    code_case = EvalCase(
        id="he",
        type="hf_benchmark",
        prompt="",
        benchmark=HfBenchmark(dataset="d", metric="code_exec"),
    )
    assert "evaluate" not in runner_project.requirements_for([code_case])


# --- presets ------------------------------------------------------------------


def test_every_preset_is_a_valid_benchmark_block():
    """A preset that does not validate is a form that fills itself with something
    the runner rejects."""
    from backend.modules.evals.models import HfBenchmark
    from backend.modules.evals.presets import PRESETS

    ids = [p["id"] for p in PRESETS]
    assert len(ids) == len(set(ids)), "preset ids must be unique"
    for preset in PRESETS:
        assert preset["why"], f"{preset['id']} does not say why it needs a preset"
        HfBenchmark(**preset["benchmark"])


def test_the_code_presets_select_the_execution_metric():
    from backend.modules.evals.presets import PRESETS

    by_id = {p["id"]: p for p in PRESETS}
    for name in ("humaneval", "mbpp"):
        assert by_id[name]["benchmark"]["metric"] == "code_exec"
        assert "container-grade" in by_id[name]["why"] or "gate" in by_id[name]["why"].lower()
