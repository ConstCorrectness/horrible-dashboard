"""LLM-as-judge: scoring an answer whose correctness is not a string match.

`Grade.judge` has been declared since the module was written and rejected by
`Expect._authorable` ever since, because a grade nothing routes fails every run and
the failure reads as the *model* being wrong. This routes it.

The reason it is worth routing at all: the existing grades score tool calls and
exact strings, which covers "did it call the right tool" and "is the number 18" and
nothing else. "Did it explain the tradeoff", "is this summary faithful", "did it
refuse for the right reason" are the questions a harness cannot answer with a regex,
and they are most of what a chat model is for.

Three rules keep a judge from being worse than no grade:

**The judge model is recorded on the result.** Two runs graded by different judges
are not comparable, and a leaderboard that does not say so presents them as if they
were. `CaseResult.detail` carries it and `judge_model` is stamped on the run.

**The rubric is the case's, and the prompt is ours.** The case supplies what to
check; the scaffolding around it — answer only with a verdict, quote the evidence,
do not reward length — is fixed here. A case that could rewrite the whole prompt
could quietly ask for something other than grading.

**A judge that fails is not a fail.** A provider error, a truncated reply, an
unparseable verdict: each returns an *error* rather than `passed=False`. Scoring a
zero because the judge could not be reached is exactly the "the case was wrong and
the model got the blame" failure this module was built around.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: Kept short and unconditional. A long rubric preamble competes with the case's
#: own rubric for the judge's attention, which shows up as the judge grading the
#: preamble's priorities rather than the case's.
SYSTEM = (
    "You are grading one answer against a rubric. Judge only what the rubric asks. "
    "Ignore length, style and confidence unless the rubric mentions them. "
    "Reply with exactly one line: PASS or FAIL, then a dash and at most twenty "
    "words of evidence quoted from the answer. Nothing else."
)

_VERDICT = re.compile(r"\b(PASS|FAIL)\b", re.IGNORECASE)


class JudgeError(RuntimeError):
    """The judge could not grade. Never scored as a failure — see the docstring."""


def _prompt(rubric: str, question: str, answer: str) -> str:
    return (
        f"Rubric:\n{rubric.strip()}\n\n"
        f"Question put to the model:\n{question.strip()}\n\n"
        f"The model's answer:\n{answer.strip() or '(the model returned nothing)'}"
    )


async def grade(
    *,
    rubric: str,
    question: str,
    answer: str,
    model: str = "",
    agent_id: str = "main",
) -> tuple[bool, str, str]:
    """`(passed, detail, judge_model)`. Raises `JudgeError` when it could not grade."""
    if not rubric.strip():
        raise JudgeError("this case selects judge grading but declares no rubric")
    if not (answer or "").strip():
        # An empty answer needs no judge, and asking one would spend a model call
        # to be told what the harness already knows. Same reasoning as `no_call`:
        # silence is a provider failure, not a wrong answer.
        return False, "the model returned nothing at all", "not asked"

    import httpx

    from backend.modules.agent import providers as P
    from backend.modules.agent.roster import resolve_provider
    from backend.modules.agent.routes import load_config

    config = load_config()
    info, endpoint = resolve_provider(config, agent_id)
    judge_model = model or str(getattr(config, "model", "") or "")
    if not judge_model:
        raise JudgeError("no model is configured to judge with")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            result = await P.chat(
                client,
                info,
                endpoint,
                judge_model,
                [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": _prompt(rubric, question, answer)},
                ],
                [],
            )
    except Exception as exc:  # noqa: BLE001 — surfaced as an error, never a zero
        raise JudgeError(f"the judge could not be reached: {exc}") from exc

    text = (result.content or "").strip()
    found = _VERDICT.search(text)
    if not found:
        # Deliberately not "no PASS means FAIL": a judge that replied with prose
        # has not said the answer is wrong, it has failed to follow the format.
        raise JudgeError(
            f"the judge did not answer PASS or FAIL (said: {text[:120]!r})"
        )

    passed = found.group(1).upper() == "PASS"
    evidence = text[found.end() :].lstrip(" -—:").strip()
    detail = f"judged by {judge_model}: {evidence or ('met the rubric' if passed else 'did not meet the rubric')}"
    return passed, detail, judge_model


def question_of(case: Any) -> str:
    """The prompt the case put to the model, for the judge's context.

    A judge shown only the answer grades it in a vacuum — "is this a good summary"
    is unanswerable without the thing being summarised.
    """
    return str(getattr(case, "prompt", "") or "")
