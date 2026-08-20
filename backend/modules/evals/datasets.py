"""Peeking at a Hub dataset without downloading it, so a benchmark can be authored
against what is actually in the columns.

Every mistake this module has made so far in a benchmark case has been the same
mistake in a different costume: **the case was wrong and the model got the blame.**
`input_template` named a column the dataset does not have; `target_column` pointed
at a field holding the worked solution rather than the answer. Both scored zero and
both looked exactly like a bad model.

The fix is to stop asking people to guess. `first-rows` from Hugging Face's
datasets-server returns the column names and a few real rows for any public dataset
without downloading a byte of it, which is enough to populate the field pickers and
to show — *before the run* — precisely what would be compared with what.

It is a fixed vendor host, so it goes over plain `httpx` like the search module's
Tavily/Brave calls rather than through `_fetch_guarded`, which is for URLs that
came from somewhere untrusted. The dataset id does reach the query string, so it is
sent as a parameter and never interpolated into the path.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.modules.evals import harness

logger = logging.getLogger(__name__)

FIRST_ROWS = "https://datasets-server.huggingface.co/first-rows"
SPLITS = "https://datasets-server.huggingface.co/splits"

#: Short: this is a form assistant, and a picker that hangs for thirty seconds is
#: worse than one that says "could not reach the Hub" and lets you type the column
#: name yourself.
TIMEOUT_S = 12.0


class PeekError(RuntimeError):
    """The Hub could not answer. Carries something worth showing the user."""


async def splits(dataset: str) -> list[dict[str, str]]:
    """Every (config, split) this dataset offers.

    Worth a call of its own because `config` is the field people miss: `gsm8k` has
    no default config, and asking for one without it fails in a way that reads as
    "the dataset is broken".
    """
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            response = await client.get(SPLITS, params={"dataset": dataset})
        except httpx.HTTPError as exc:
            raise PeekError(f"could not reach the Hub: {exc}") from exc
    if response.status_code == 404:
        raise PeekError(f"no dataset {dataset!r} on the Hub, or it is gated")
    if response.status_code >= 400:
        raise PeekError(_detail(response, f"the Hub returned {response.status_code}"))
    return [
        {"config": str(s.get("config") or ""), "split": str(s.get("split") or "")}
        for s in (response.json().get("splits") or [])
    ]


async def first_rows(
    dataset: str, config: str = "", split: str = "train", limit: int = 3
) -> dict[str, Any]:
    """Column names and a few real rows.

    The rows are what make the editor's comparison preview honest: it renders the
    template and applies the regexes to an actual row rather than to an example
    somebody wrote in a docstring.
    """
    params = {"dataset": dataset, "split": split}
    if config:
        params["config"] = config
    else:
        # The server requires a config. Resolving it here rather than making the
        # caller do it is the whole point — "which config?" is not a question the
        # person filling in the form can answer yet.
        found = await splits(dataset)
        if not found:
            raise PeekError(f"{dataset!r} reports no splits")
        params["config"] = found[0]["config"]
        if not any(
            s["split"] == split for s in found if s["config"] == params["config"]
        ):
            params["split"] = found[0]["split"]

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            response = await client.get(FIRST_ROWS, params=params)
        except httpx.HTTPError as exc:
            raise PeekError(f"could not reach the Hub: {exc}") from exc

    if response.status_code >= 400:
        raise PeekError(_detail(response, f"the Hub returned {response.status_code}"))

    payload = response.json()
    columns = [
        str(f.get("name") or "")
        for f in (payload.get("features") or [])
        if f.get("name")
    ]
    rows = [r.get("row") or {} for r in (payload.get("rows") or [])][:limit]
    return {
        "dataset": dataset,
        "config": params["config"],
        "split": params["split"],
        "columns": columns,
        "rows": rows,
    }


def _detail(response: httpx.Response, fallback: str) -> str:
    """The Hub's own error text when there is one.

    Worth digging out: its messages name the actual problem ("Config name is
    missing", "Dataset is gated"), and replacing them with a status code would
    throw away the only useful part of the response.
    """
    try:
        body = response.json()
    except ValueError:
        return fallback
    for key in ("error", "detail", "message"):
        if body.get(key):
            return str(body[key])
    return fallback


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
