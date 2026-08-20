"""Comparing what a model did with what the case expected.

Pure functions over recorded calls: no I/O, no provider, no connection. That is
deliberate — grading is the part most likely to be argued about, and an argument
about grading should be settleable by reading a table of inputs and outputs rather
than by running a model.

## Why there are six grades and not one

A single "did it call the right tool" check is wrong in both directions at once.
It fails a model that called the right tool with a harmless extra argument, and it
passes a model that fired a tool at a question that wanted an answer. Both of those
are the *normal* behaviour of a small model, so a harness that cannot tell them
apart measures almost nothing.

- `exact` — name and arguments identical. For a tool where a stray argument is a
  real bug (a destructive one, or one where the argument *is* the decision).
- `name_only` — the right tool, arguments unchecked. Honest about what you are
  measuring when the arguments are free text.
- `subset` — every expected argument present and equal; extras tolerated. The
  default, because most tools have optional arguments a model may reasonably fill.
- `sequence` — the expected calls, in order, as a subsequence of what happened.
  A subsequence rather than an exact list: a model that looked something up before
  acting did not do it wrong.
- `no_call` — no tool at all, **and an actual answer**. The negative case. An
  empty reply fails it: saying nothing is not answering, and scoring it as a
  pass turns a dead provider into a clean-looking scoreboard.
- `judge` — an LLM grades the final answer against a rubric. The escape hatch, and
  the only grade whose verdict is not reproducible from the record alone, which is
  why it is never the default.

## Argument comparison

Compared after a light normalisation (see `_norm`), because a model that answers
`"true"` where the schema wants `true`, or ` terminal ` where the fixture says
`terminal`, has picked the right tool and the right argument. Being strict there
measures the model's JSON formatting, not its tool selection — and formatting is
what the provider's grammar is for.

Numbers are the exception that keeps a little strictness: `1` and `1.0` are the
same value, but `1` and `"1"` are compared as strings after normalisation, so a
schema-typed integer answered as a string still matches. If that ever hides a real
bug, the case wants `exact`.
"""

from __future__ import annotations

from typing import Any

from backend.modules.evals.models import CaseResult, EvalCase, ToolCall


def _norm(value: Any) -> Any:
    """Normalise one argument value for comparison.

    Strings are stripped and lowercased; numbers keep their numeric identity so
    `1` and `1.0` match; containers normalise elementwise. `None` and a missing key
    are *not* conflated — "passed null explicitly" and "did not pass it" are
    different choices, and a tool that treats them the same is the tool's business.
    """
    if isinstance(value, bool):
        # Tagged, and checked before the number branch. `bool` is a subclass of
        # `int` in Python and `True == 1.0`, so simply returning the bool unchanged
        # would still compare equal to the number one — `verbose=true` matching
        # `verbose=1`, which is a different argument.
        return ("bool", value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        # A number that arrived as a string still compares equal to the number.
        try:
            return float(text)
        except ValueError:
            return text
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in sorted(value.items())}
    return value


def _args_match(
    expected: dict[str, Any], actual: dict[str, Any], *, exact: bool
) -> bool:
    if exact and set(expected) != set(actual):
        return False
    for key, want in expected.items():
        if key not in actual:
            return False
        if _norm(want) != _norm(actual[key]):
            return False
    return True


def _call_matches(expected: ToolCall, actual: ToolCall, grade: str) -> bool:
    if expected.name != actual.name:
        return False
    if grade == "name_only":
        return True
    return _args_match(expected.arguments, actual.arguments, exact=grade == "exact")


def _describe(calls: list[ToolCall]) -> str:
    if not calls:
        return "no calls"
    return ", ".join(
        c.name if not c.arguments else f"{c.name}({_short_args(c.arguments)})"
        for c in calls
    )


def _short_args(args: dict[str, Any]) -> str:
    """Arguments, short enough for a scoreboard row.

    A `files.write` call carries a whole file in its arguments, and a detail line
    that is 4 KB of source is a detail line nobody reads.
    """
    parts = []
    for key, value in list(args.items())[:3]:
        text = str(value)
        if len(text) > 24:
            text = text[:21] + "…"
        parts.append(f"{key}={text}")
    if len(args) > 3:
        parts.append("…")
    return ", ".join(parts)


def grade_case(case: EvalCase, actual: list[ToolCall], answer: str) -> tuple[bool, str]:
    """Whether the model got this case right, and one line saying why.

    Returns `(passed, detail)`. The detail is written for the person reading a
    failing row, so it always names what was expected *and* what happened — a
    detail that says only "failed" sends them to re-run the case by hand, which is
    the thing the harness exists to avoid.
    """
    grade = case.expect.grade
    expected = case.expect.calls

    if grade == "no_call":
        if actual:
            return False, f"expected no tool call; called {_describe(actual)}"
        if not (answer or "").strip():
            # An empty reply is not "correctly answered without calling a tool" —
            # it is the model saying nothing, which usually means the provider
            # failed. Treating it as a pass is worse than a wrong score: it makes a
            # total provider failure look like a suite of clean negatives, and it
            # hides the positives' failures behind a respectable-looking number.
            #
            # Found exactly that way. A sweep where every single case came back
            # empty scored 6/13 — every `no_call` case "passing" — and the real
            # cause (a provider error swallowed mid-stream) was invisible until the
            # answers were read by hand.
            return (
                False,
                "the model returned nothing at all — no answer and no tool call",
            )
        return True, "answered without calling a tool, as expected"

    if grade == "judge":
        # Decided elsewhere: the judge needs a provider, and this module is pure.
        # Reaching here means the runner did not route it, which is a bug in the
        # runner rather than a failing case — say so instead of scoring it.
        return False, "judge grading was not run for this case"

    if not expected:
        return (
            False,
            "case declares no expected calls; use grade 'no_call' to assert none",
        )

    if grade == "sequence":
        # A subsequence, not an exact list: a model that read something before
        # acting has not done it wrong, it has been careful.
        remaining = list(actual)
        for want in expected:
            while remaining and not _call_matches(want, remaining[0], "subset"):
                remaining.pop(0)
            if not remaining:
                return (
                    False,
                    f"expected the sequence {_describe(expected)}; got {_describe(actual)}",
                )
            remaining.pop(0)
        return True, f"called {_describe(expected)} in order"

    # exact / name_only / subset: every expected call must appear somewhere.
    unmatched: list[ToolCall] = []
    pool = list(actual)
    for want in expected:
        hit = next((c for c in pool if _call_matches(want, c, grade)), None)
        if hit is None:
            unmatched.append(want)
        else:
            pool.remove(hit)

    if unmatched:
        # Name the near miss when there is one: "called open_pane with the wrong
        # id" is a different problem from "never called open_pane", and the
        # distinction is most of what you learn from a failing suite.
        near = [c for c in actual if any(c.name == u.name for u in unmatched)]
        if near:
            return (
                False,
                f"called {_describe(near)} but expected {_describe(unmatched)}",
            )
        return (
            False,
            f"expected {_describe(unmatched)}; called {_describe(actual) if actual else 'nothing'}",
        )

    if pool:
        # Extra calls are worth reporting even on a pass: a model that opened the
        # right pane *and* deleted a file has not really passed, and the person
        # reading the row should get to decide that.
        return True, f"called {_describe(expected)} (plus {_describe(pool)})"
    return True, f"called {_describe(expected)}"


def result_for(
    case: EvalCase,
    actual: list[ToolCall],
    answer: str,
    **fields: Any,
) -> CaseResult:
    """Grade a case and build its result row."""
    passed, detail = grade_case(case, actual, answer)
    return CaseResult(
        case_id=case.id,
        passed=passed,
        grade=case.expect.grade,
        detail=detail,
        expected=case.expect.calls,
        actual=actual,
        answer=answer,
        **fields,
    )
