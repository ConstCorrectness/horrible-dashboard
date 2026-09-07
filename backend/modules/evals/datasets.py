"""Comparison preview: what a benchmark case would actually compare, for a real row.

The Hub access this is built on moved to `backend.modules.datasets.hub` — a
dataset is not an eval concept, and the recipe form needs exactly the same three
calls. `splits`, `first_rows` and `PeekError` are re-exported here so every caller
that already imported them from this module keeps working.

What stays is the part that *is* eval-specific: `compare_preview` runs the same
`extract` and `normalise` the generated harness runs — imported, not
reimplemented — so a regex that will silently fail to match at run time silently
fails to match here too, visibly, before the benchmark burns ten minutes scoring
zero.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.datasets.hub import (
    FIRST_ROWS,
    SPLITS,
    TIMEOUT_S,
    PeekError,
    first_rows,
    splits,
)
from backend.modules.evals import harness

logger = logging.getLogger(__name__)

__all__ = [
    "FIRST_ROWS",
    "SPLITS",
    "TIMEOUT_S",
    "PeekError",
    "compare_preview",
    "first_rows",
    "splits",
]


#: Separators datasets use to mark the final answer at the end of a worked
#: solution, and the regex that pulls the answer out of each.
_SEPARATORS: tuple[tuple[str, str], ...] = (
    ("####", r"####\s*(.+)"),
    ("The answer is", r"[Tt]he answer is\s*(.+)"),
    ("Answer:", r"Answer:\s*(.+)"),
)


def _separator_hint(reference: str) -> str:
    """A suggested `target_regex` when the reference looks like worked reasoning
    ending in a marked answer. Empty when nothing recognisable is there — guessing
    at a pattern would be worse than saying nothing."""
    tail = reference[-200:]
    for marker, pattern in _SEPARATORS:
        if marker in tail:
            return f"target_regex = {pattern}"
    return ""


def compare_preview(
    row: dict[str, Any],
    *,
    input_template: str,
    target_column: str,
    target_regex: str,
    prediction_regex: str,
    sample_prediction: str = "",
) -> dict[str, Any]:
    """What this case would actually compare, for one real row.

    The point of the whole module, in one function. It runs the *same* `extract`
    and `normalise` the generated harness runs — imported, not reimplemented — so
    the preview cannot flatter the case. A regex that will silently fail to match
    at run time silently fails to match here too, and that is visible before the
    benchmark burns ten minutes scoring zero.
    """
    problems: list[str] = []

    try:
        prompt = input_template.format(**row)
    except KeyError as exc:
        prompt = ""
        problems.append(
            f"input_template names {exc}, which this dataset does not have. "
            f"Columns are: {', '.join(row)}"
        )
    except (IndexError, ValueError) as exc:
        prompt = ""
        problems.append(f"input_template is malformed: {exc}")

    if target_column not in row:
        problems.append(
            f"target_column {target_column!r} is not in this dataset. "
            f"Columns are: {', '.join(row)}"
        )
        raw_reference = ""
    else:
        raw_reference = str(row[target_column])

    reference = harness.extract(raw_reference, target_regex)
    if target_regex and reference == raw_reference and raw_reference:
        # Not fatal — `extract` deliberately degrades to "compare the whole thing"
        # — but it is almost always a mistake, and it is the specific mistake that
        # produced a 0.000 nobody could explain.
        problems.append(
            "target_regex did not match, so the WHOLE reference will be compared. "
            "That is usually a scoring of zero waiting to happen."
        )
    elif not target_regex and raw_reference:
        # The warning that matters most, and the one an earlier version of this
        # function missed: the original 0.000 had NO regex at all, so there was no
        # failed match to complain about. An answer column carrying a separator or
        # several lines of working is a reference no model will ever reproduce, and
        # saying so here is the difference between a five-second fix and a
        # ten-minute run that scores zero for reasons nobody can see.
        hint = _separator_hint(raw_reference)
        if hint:
            problems.append(
                f"no target_regex, so the whole answer column is the reference — and "
                f"it looks like the worked solution rather than the answer. "
                f"Try {hint}"
            )
        elif "\n" in raw_reference or len(raw_reference) > 160:
            problems.append(
                "no target_regex, so the whole answer column is the reference. It is "
                "long or multi-line, which usually means it holds more than the "
                "answer — check the reference below is what you meant to grade."
            )

    prediction = harness.extract(sample_prediction, prediction_regex)
    if prediction_regex and sample_prediction and prediction == sample_prediction:
        problems.append("prediction_regex did not match your sample reply")

    return {
        "prompt": prompt,
        "reference_raw": raw_reference,
        "reference": reference,
        "reference_normalised": harness.normalise(reference),
        "prediction": prediction,
        "prediction_normalised": harness.normalise(prediction) if prediction else "",
        "problems": problems,
    }
