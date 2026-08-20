"""Known-good benchmark blocks, for datasets whose answer column is a trap.

This file is the GSM8K lesson written down. Its `answer` column holds the *worked
solution* and marks the final answer with `####`:

    Janet sells 16 - 3 - 4 = 9 duck eggs a day.
    She makes 9 * 2 = $18 every day at the farmer's market.
    #### 18

A case that grades that column as-is is asking whether the model reproduced the
dataset's own prose. No model does. The first real run of this module scored
**0.000** for exactly that reason, against a model that was answering correctly —
and the same model on the same rows scored **1.000** once the regexes were right.

A preset is cheaper than a paragraph nobody reads at the moment they need it. These
are starting points the form fills in, not a closed list: the fields stay editable,
and a dataset with no preset is authored by peeking at its columns.

Each entry is a partial `HfBenchmark`. Anything it omits keeps the model's default.
"""

from __future__ import annotations

from typing import Any

#: Pulls the last number out of a reply, tolerating a trailing full stop, currency
#: symbol or unit. The common shape for a model that reasons out loud and finishes
#: with the figure.
LAST_NUMBER = r"(-?[\d.,]+)[^\d]*$"

#: Everything after GSM8K's `####` separator.
AFTER_HASHES = r"####\s*(.+)"

PRESETS: list[dict[str, Any]] = [
    {
        "id": "gsm8k",
        "label": "GSM8K — grade-school maths",
        "why": (
            "The answer column holds the worked solution and marks the final answer "
            "with ####, so it must be extracted or every row scores zero."
        ),
        "benchmark": {
            "dataset": "openai/gsm8k",
            "config": "main",
            "split": "test[:50]",
            "input_template": "{question}",
            "target_column": "answer",
            "target_regex": AFTER_HASHES,
            "prediction_regex": LAST_NUMBER,
            "metric": "contains",
            "limit": 50,
            "threshold": 0.3,
            "system": "Solve the problem. End your reply with just the final number.",
        },
    },
    {
        "id": "mmlu",
        "label": "MMLU — multiple choice",
        "why": (
            "Choices live in a list column and the answer is an INDEX, not a letter. "
            "The template spells the options out and the model is pinned to a single "
            "letter, because a free-form reply cannot be compared with an index."
        ),
        "benchmark": {
            "dataset": "cais/mmlu",
            "config": "all",
            "split": "test[:50]",
            "input_template": "{question}\n\nOptions:\n{choices}",
            "target_column": "answer",
            "target_regex": "",
            "prediction_regex": r"\b([A-D])\b(?!.*\b[A-D]\b)",
            "metric": "contains",
            "limit": 50,
            "threshold": 0.25,
            "system": ("Answer with a single letter: A, B, C or D. Nothing else."),
        },
    },
    {
        "id": "hellaswag",
        "label": "HellaSwag — sentence completion",
        "why": "The label is an index into `endings`; the model is pinned to that index.",
        "benchmark": {
            "dataset": "Rowan/hellaswag",
            "config": "",
            "split": "validation[:50]",
            "input_template": "{ctx}\n\nEndings:\n{endings}",
            "target_column": "label",
            "target_regex": "",
            "prediction_regex": r"([0-3])(?!.*[0-3])",
            "metric": "contains",
            "limit": 50,
            "threshold": 0.25,
            "system": "Reply with the number of the best ending: 0, 1, 2 or 3. Nothing else.",
        },
    },
    {
        "id": "truthfulqa",
        "label": "TruthfulQA — generation",
        "why": (
            "Free-form answers, so there is no exact match to make. Graded on whether "
            "the best known answer appears; treat the score as a signal, not a fact."
        ),
        "benchmark": {
            "dataset": "truthfulqa/truthful_qa",
            "config": "generation",
            "split": "validation[:50]",
            "input_template": "{question}",
            "target_column": "best_answer",
            "target_regex": "",
            "prediction_regex": "",
            "metric": "contains",
            "limit": 50,
            "threshold": 0.2,
            "system": "Answer truthfully and briefly.",
        },
    },
]
