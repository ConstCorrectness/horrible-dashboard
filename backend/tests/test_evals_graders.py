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


def test_a_case_cannot_select_judge_grading():
    """`judge` is declared but nothing routes it — the judge needs a provider and
    this module is deliberately pure — so a case asking for it fails every time it
    runs, and the failure reads as the *model* getting it wrong. Rejected at the
    case, where the mistake costs nothing, rather than twenty minutes into a sweep.
    """
    with pytest.raises(ValidationError):
        case("judge", [], rubric="is it polite")


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
