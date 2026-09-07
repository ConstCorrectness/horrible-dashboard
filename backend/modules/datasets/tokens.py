"""How long are these examples, really — asked before the run rather than after.

`max_length` is the single biggest lever on memory after batch size, and it is also
the setting whose mistakes are completely silent. Set it to 1024 against a dataset
whose median example is 3000 tokens and `trl` truncates two thirds of every example
away. Nothing errors. Loss goes down. The model learns the first third of answers.

So: sample rows, render them the way the recipe would, count tokens, and report the
fraction over the limit. That last number is the one the pane exists to show.

Two reuses, both deliberate:

- **`interpretability.tokenizer.Counter`**, not a new counter. It already resolves a
  repo from a model name, falls back to a chars/4 estimate, and — crucially — knows
  whether the answer is `exact`. A second implementation would eventually disagree
  with the one the lens uses, about the same model.
- **`formats`' column map**, so the text counted is the text that would be trained
  on. Counting `str(row)` would include JSON punctuation and column names, inflating
  every number by a quarter and making the limit look closer than it is.

Named `tokens.py` and not `tokenize.py`: the latter shadows a stdlib module, and the
import that breaks is somebody else's.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.modules.datasets.models import TokenStatsModel

logger = logging.getLogger(__name__)

#: Buckets for the histogram, in tokens. Log-ish rather than linear because example
#: lengths are: a linear histogram of a corpus with a long tail is one tall bar and
#: nine empty ones.
_BUCKETS = (0, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768)


def row_text(row: dict[str, Any], fmt: str, columns: dict[str, str]) -> str:
    """The text this row contributes, as the recipe would render it.

    Mirrors `formats.formatting_source` — the reshapers there build chat turns, and
    what reaches the tokenizer is the concatenated content of those turns. Close
    enough to be useful (a chat template adds a few tokens per turn), and honest
    about which columns are involved, which is the part that matters.
    """
    if fmt == "raw_text":
        return str(row.get(columns.get("text", "text"), "") or "")
    if fmt in ("chatml", "sharegpt"):
        turns = row.get(columns.get("messages", "messages"))
        if not isinstance(turns, list):
            return ""
        parts = []
        for turn in turns:
            if isinstance(turn, dict):
                parts.append(str(turn.get("content") or turn.get("value") or ""))
        return "\n".join(parts)
    if fmt == "alpaca":
        pieces = [
            str(row.get(columns.get("instruction", "instruction"), "") or ""),
            str(row.get(columns.get("input", ""), "") or "")
            if columns.get("input")
            else "",
            str(row.get(columns.get("output", "output"), "") or ""),
        ]
        return "\n\n".join(p for p in pieces if p)
    if fmt == "preference":
        # The longest of the two completions plus the prompt: that is the sequence
        # the trainer must fit, and averaging them would understate it.
        prompt = str(row.get(columns.get("prompt", "prompt"), "") or "")
        chosen = str(row.get(columns.get("chosen", "chosen"), "") or "")
        rejected = str(row.get(columns.get("rejected", "rejected"), "") or "")
        return prompt + "\n" + max(chosen, rejected, key=len)
    # Unknown shape: we do not know which column holds the content, so measure
    # everything.
    #
    # **Every value, not just the strings.** Restricting to `str` looks tidy and
    # produces a badly wrong number: a dataset whose text sits in a list-of-dicts
    # column (LDJnr/Capybara is exactly this) then measures only its little
    # `source` label and reports a median of 4 tokens and "0% truncated" — a
    # confident, specific, useless answer, which is worse than an obviously rough
    # one. Serializing non-strings overcounts slightly by including JSON
    # punctuation; that errs toward warning about truncation rather than away
    # from it, which is the right direction for this particular number.
    parts: list[str] = []
    for value in row.values():
        if isinstance(value, str):
            parts.append(value)
        elif value is not None and not isinstance(value, bool):
            parts.append(json.dumps(value, default=str))
    return "\n".join(parts)


def _percentile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def _histogram(counts: list[int]) -> list[dict[str, int]]:
    out: list[dict[str, int]] = []
    for index, low in enumerate(_BUCKETS):
        high = _BUCKETS[index + 1] if index + 1 < len(_BUCKETS) else None
        hits = sum(1 for c in counts if c >= low and (high is None or c < high))
        if hits or high is None or low < 4096:
            out.append({"from": low, "to": high or 0, "count": hits})
    return out


async def token_stats(
    rows: list[dict[str, Any]],
    *,
    fmt: str,
    columns: dict[str, str],
    model: str = "",
    max_length: int = 1024,
) -> TokenStatsModel:
    """Length statistics over a sample, and the fraction that would be truncated."""
    from backend.modules.interpretability.tokenizer import Counter

    counter = await Counter.create(model)
    counts = sorted(counter.count(row_text(row, fmt, columns)) for row in rows)
    counts = [c for c in counts if c > 0]
    if not counts:
        return TokenStatsModel(
            sampled=0,
            tokenizer=counter.repo or "",
            exact=False,
            note=(
                "no text could be extracted from the sampled rows — check the "
                "column map before trusting the format verdict."
            ),
        )

    over = sum(1 for c in counts if c > max_length) / len(counts)
    note = ""
    if fmt in ("", "unknown"):
        # Say so, loudly. A precise-looking percentage measured over "every column
        # serialized" is not the same number as one measured over the text that
        # would actually be trained on, and presenting them identically is how a
        # reassuring 0% gets believed.
        note = (
            "this dataset's shape was not recognised, so every column was measured "
            "rather than the text that would actually be trained on — treat these "
            "as a rough upper bound. Map the columns to get a real number. "
        )
    if not counter.exact:
        note += (
            f"Estimated at ~4 characters per token{' (no model named)' if not model else ''}"
            "; name the base model for exact counts."
        )
    return TokenStatsModel(
        sampled=len(counts),
        tokenizer=counter.repo or "",
        exact=counter.exact,
        note=note,
        min=counts[0],
        max=counts[-1],
        mean=round(sum(counts) / len(counts), 1),
        p50=_percentile(counts, 0.5),
        p95=_percentile(counts, 0.95),
        over_limit=round(over, 4),
        histogram=_histogram(counts),
    )
