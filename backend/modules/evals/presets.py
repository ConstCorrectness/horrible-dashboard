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
    {
        "id": "humaneval",
        "label": "HumanEval — code generation",
        "why": (
            "Code cannot be graded by string match: two correct solutions to one "
            "problem share almost no characters. `code_exec` runs the model's "
            "completion against the dataset's own tests instead. Needs "
            "HORRIBLE_ENABLE_EVAL_CODE_EXEC=1 — it executes model-written code on "
            "this machine, isolated but not container-grade."
        ),
        "benchmark": {
            "dataset": "openai/openai_humaneval",
            "config": "",
            "split": "test[:20]",
            "input_template": "{prompt}",
            # Unused by `code_exec` (the tests decide), but the model requires a
            # column that exists, and pointing it at the reference solution is the
            # least surprising choice.
            "target_column": "canonical_solution",
            "target_regex": "",
            "prediction_regex": "",
            "metric": "code_exec",
            "test_column": "test",
            "entry_point_column": "entry_point",
            "limit": 20,
            "threshold": 0.3,
            "system": (
                "Complete the function. Reply with the function body or the whole "
                "function, in a single Python code block and nothing else."
            ),
        },
    },
    {
        "id": "mbpp",
        "label": "MBPP — basic Python problems",
        "why": (
            "Like HumanEval but the tests live in `test_list` as a LIST of assert "
            "statements rather than a `check()` function, and there is no signature "
            "stub — the prompt is a sentence. Same execution gate."
        ),
        "benchmark": {
            "dataset": "google-research-datasets/mbpp",
            "config": "full",
            "split": "test[:20]",
            "input_template": "{text}",
            "target_column": "code",
            "target_regex": "",
            "prediction_regex": "",
            "metric": "code_exec",
            "test_column": "test_list",
            # MBPP has no entry-point column: the tests call the function by name
            # themselves, so nothing needs to be prepended.
            "entry_point_column": "",
            "limit": 20,
            "threshold": 0.3,
            "system": (
                "Write the function described. Reply with one Python code block "
                "and nothing else."
            ),
        },
    },
    {
        "id": "arc",
        "label": "ARC Challenge — science multiple choice",
        "why": (
            "The answer column holds a LETTER (`A`–`D`) while the choices are a "
            "nested {text, label} structure, so the prompt has to render them and "
            "the reply has to be reduced to a single letter."
        ),
        "benchmark": {
            "dataset": "allenai/ai2_arc",
            "config": "ARC-Challenge",
            "split": "test[:50]",
            "input_template": "{question}\n\nChoices: {choices}",
            "target_column": "answerKey",
            "target_regex": "",
            "prediction_regex": r"([A-D])",
            "metric": "exact_match",
            "limit": 50,
            "threshold": 0.3,
            "system": "Answer with the single letter of the correct choice.",
        },
    },
    {
        "id": "gpqa",
        "label": "GPQA Diamond — graduate-level science",
        "why": (
            "Deliberately hard: strong models score barely above chance, so treat a "
            "low number as expected rather than as a broken harness. The correct "
            "answer is a full sentence in its own column, not an index."
        ),
        "benchmark": {
            "dataset": "Idavidrein/gpqa",
            "config": "gpqa_diamond",
            "split": "train[:50]",
            "input_template": "{Question}",
            "target_column": "Correct Answer",
            "target_regex": "",
            "prediction_regex": "",
            "metric": "contains",
            "limit": 50,
            "threshold": 0.2,
            "system": "Answer concisely with the correct option.",
        },
    },
    {
        "id": "ifeval",
        "label": "IFEval — instruction following",
        "why": (
            "Measures whether a reply OBEYS its instruction (word counts, formats, "
            "forbidden words), which no string comparison captures. Graded here as a "
            "rough `contains` against the prompt's own key phrase — for the real "
            "verifiable-instruction scoring, use a judge case or the lm-eval runner."
        ),
        "benchmark": {
            "dataset": "google/IFEval",
            "config": "",
            "split": "train[:50]",
            "input_template": "{prompt}",
            "target_column": "prompt",
            "target_regex": "",
            "prediction_regex": "",
            "metric": "contains",
            "limit": 50,
            "threshold": 0.2,
            "system": "Follow every instruction in the prompt exactly.",
        },
    },
]
