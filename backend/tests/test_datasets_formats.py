"""Format detection: the verdicts, and the confusions that would be silent."""

from __future__ import annotations

import pytest

from backend.modules.datasets import formats


def test_chatml_is_told_from_sharegpt_by_the_keys_inside_the_list():
    """The two differ only *inside* the message items, which a column-name check
    cannot see — and getting it wrong yields empty turns, not an error."""
    chatml = formats.detect(
        ["messages"],
        [{"messages": [{"role": "user", "content": "hi"}]}] * 3,
    )
    sharegpt = formats.detect(
        ["conversations"],
        [{"conversations": [{"from": "human", "value": "hi"}]}] * 3,
    )
    assert chatml.format == "chatml"
    assert sharegpt.format == "sharegpt"
    # Same column name, different dialect: the item keys must decide.
    same_column = formats.detect(
        ["messages"], [{"messages": [{"from": "human", "value": "hi"}]}] * 2
    )
    assert same_column.format == "sharegpt"


def test_preference_wins_over_alpaca_when_both_could_match():
    """A preference set carries a `prompt`/`instruction` column too. Read as Alpaca
    it trains an SFT objective on data collected to express a preference, and
    nothing anywhere says so."""
    detection = formats.detect(
        ["prompt", "chosen", "rejected"],
        [{"prompt": "q", "chosen": "good", "rejected": "bad"}] * 4,
    )
    assert detection.format == "preference"
    assert detection.columns == {
        "chosen": "chosen",
        "rejected": "rejected",
        "prompt": "prompt",
    }


def test_alpaca_reports_its_optional_input_column():
    detection = formats.detect(
        ["instruction", "input", "output"],
        [{"instruction": "Sum", "input": "1 2", "output": "3"}] * 2,
    )
    assert detection.format == "alpaca"
    assert detection.columns["input"] == "input"
    assert detection.certain


def test_a_text_column_holding_non_strings_is_unknown_not_raw_text():
    """Named `text`, holds a list. Training on its repr is the failure mode."""
    detection = formats.detect(["text"], [{"text": [{"a": 1}]}] * 3)
    assert detection.format == "unknown"
    assert "rather than strings" in detection.reason


def test_confidence_is_the_fraction_of_rows_that_actually_matched():
    detection = formats.detect(
        ["text"],
        [{"text": "a"}, {"text": ""}, {"text": "c"}, {"text": None}],
    )
    assert detection.format == "raw_text"
    assert detection.confidence == pytest.approx(0.5)


def test_no_rows_is_a_refusal_not_a_guess_from_column_names():
    detection = formats.detect(["messages"], [])
    assert detection.format == "unknown"
    assert not detection.certain


def test_sft_refuses_a_preference_set_and_says_why():
    detection = formats.detect(
        ["prompt", "chosen", "rejected"],
        [{"prompt": "q", "chosen": "a", "rejected": "b"}],
    )
    adaptation = formats.adapt(detection, "sft")
    assert not adaptation.ok
    assert "rejected" in adaptation.problem


def test_dpo_accepts_a_preference_set_and_refuses_alpaca():
    preference = formats.detect(
        ["prompt", "chosen", "rejected"],
        [{"prompt": "q", "chosen": "a", "rejected": "b"}],
    )
    alpaca = formats.detect(
        ["instruction", "output"], [{"instruction": "i", "output": "o"}]
    )
    assert formats.adapt(preference, "dpo").ok
    refusal = formats.adapt(alpaca, "dpo")
    assert not refusal.ok
    assert "preference" in refusal.problem


def test_an_unknown_shape_refuses_rather_than_inventing_a_map():
    detection = formats.Detection("unknown", 0.0, "nothing matched")
    adaptation = formats.adapt(detection, "sft")
    assert not adaptation.ok
    assert adaptation.columns == {}


@pytest.mark.parametrize("task", sorted(formats.TASK_FORMATS))
def test_every_task_names_only_known_formats(task: str):
    """A typo here would make a task permanently unsatisfiable, with the refusal
    blaming the user's dataset."""
    for fmt in formats.TASK_FORMATS[task]:
        assert fmt in formats.FORMATS


def test_shapes_that_need_reshaping_say_so_and_emit_real_code():
    for fmt, columns in (
        ("sharegpt", {"messages": "conversations"}),
        ("alpaca", {"instruction": "instruction", "output": "output"}),
    ):
        adaptation = formats.adapt(formats.Detection(fmt, 1.0, "", columns), "sft")
        assert adaptation.ok and adaptation.needs_formatting
        source = formats.formatting_source(fmt, columns)
        assert "dataset.map(to_messages)" in source
        compile(source, "<generated>", "exec")  # it must be real Python


def test_chatml_and_raw_text_need_no_reshaping():
    for fmt, columns in (
        ("chatml", {"messages": "messages"}),
        ("raw_text", {"text": "text"}),
    ):
        adaptation = formats.adapt(formats.Detection(fmt, 1.0, "", columns), "sft")
        assert adaptation.ok and not adaptation.needs_formatting


def test_text_field_follows_the_reshape():
    """Both reshapers emit `messages`, so trl must be pointed at that and not at
    the original column — pointing at `conversations` after the map trains on a
    column that no longer holds what it did."""
    assert (
        formats.text_field_for("sharegpt", {"messages": "conversations"}) == "messages"
    )
    assert (
        formats.text_field_for("alpaca", {"instruction": "instruction"}) == "messages"
    )
    assert formats.text_field_for("raw_text", {"text": "body"}) == "body"


# --- token measurement --------------------------------------------------------


def test_an_unrecognised_shape_measures_every_column_not_just_the_strings():
    """Found by driving the real UI against `LDJnr/Capybara`.

    Its text sits in a list-of-dicts column, so measuring only `str` values found
    nothing but the little `source` label and reported a median of 4 tokens and
    "0% truncated at 1024" — a confident, specific, useless number. The same rows
    measured properly are ~85% over. A rough upper bound is far better than a
    precise lie, and the note says which it is.
    """
    from backend.modules.datasets.tokens import row_text

    row = {
        "source": "General-Instruct",
        "conversation": [{"input": "q " * 200, "output": "a " * 200}],
    }
    text = row_text(row, "unknown", {})
    assert len(text) > 500, "the list column must be measured, not skipped"
    assert "General-Instruct" in text


def test_a_known_shape_measures_only_what_would_be_trained_on():
    """The opposite guarantee: once the shape IS known, measuring the whole row
    would include JSON punctuation and column names and overstate every number."""
    from backend.modules.datasets.tokens import row_text

    row = {
        "messages": [{"role": "user", "content": "hello"}],
        "num_turns": 1,
        "source": "somewhere-irrelevant",
    }
    assert row_text(row, "chatml", {"messages": "messages"}) == "hello"


def test_stats_on_an_unknown_shape_say_they_are_a_rough_bound():
    import asyncio

    from backend.modules.datasets.tokens import token_stats

    rows = [{"body": {"nested": "x " * 300}} for _ in range(5)]
    stats = asyncio.run(token_stats(rows, fmt="unknown", columns={}, max_length=64))
    assert stats.over_limit == 1.0
    assert "not recognised" in stats.note
    assert not stats.exact


# --- prompt/completion: the shape every reasoning dataset is spelled in --------


def _limo_rows(n: int = 5) -> list[dict]:
    """`GAIR/LIMO`'s real columns: a question, a long chain of thought, and the
    bare final answer."""
    return [
        {
            "question": "Find the last three digits of the product of the roots.",
            "solution": "Okay, so I need to " + "x" * 3000,
            "answer": "25",
        }
        for _ in range(n)
    ]


def test_question_solution_is_prompt_completion_not_unknown():
    """The hole this closes reached all the way to a stack trace.

    Alpaca demands the literal column `instruction`, so a dataset spelled
    `question`/`solution` — LIMO, OpenR1-Math, and most of the reasoning sets
    anyone post-trains on — matched nothing, reported `unknown`, inferred no
    column map, and the emitted `SFTConfig` fell back to trl's default `text`
    column. The run died on `KeyError: 'text'` after the base model had loaded.
    """
    from backend.modules.datasets import formats

    detection = formats.detect(["question", "solution", "answer"], _limo_rows())
    assert detection.format == "prompt_completion"
    assert detection.certain
    assert detection.columns == {"prompt": "question", "completion": "solution"}


def test_the_completion_column_is_measured_not_ranked():
    """`solution` and `answer` are both completion-shaped names, and LIMO has
    both: one is the entire chain of thought, the other is the string `25`.

    Taking the first off a fixed list would train the model to emit the final
    number and drop the reasoning — the exact opposite of post-training on a
    reasoning dataset, with no error anywhere and a loss curve that looks fine.
    So the choice is made by measuring the rows, and the measurement is in the
    reason where it can be overruled on sight.
    """
    from backend.modules.datasets import formats

    detection = formats.detect(["question", "solution", "answer"], _limo_rows())
    assert detection.columns["completion"] == "solution"
    assert "averages" in detection.reason
    assert "`answer`" in detection.reason, "the rejected candidate must be named"


def test_prompt_completion_is_renamed_never_concatenated():
    """trl trains `{prompt, completion}` natively and masks the prompt out of the
    loss. Flattening both into one `text` column would train the model to predict
    the question as well, so the reshape is a rename and nothing else."""
    from backend.modules.datasets import formats

    detection = formats.detect(["question", "solution", "answer"], _limo_rows())
    adaptation = formats.adapt(detection, "sft")
    assert adaptation.ok and adaptation.needs_formatting
    source = formats.formatting_source(detection.format, adaptation.columns)
    assert "rename_columns" in source
    assert "'question': 'prompt'" in source
    assert "'solution': 'completion'" in source
    # No text field: naming one would point trl at a single column and throw the
    # prompt masking away.
    assert formats.text_field_for(detection.format, adaptation.columns) == ""


def test_already_canonical_columns_emit_no_reshape():
    """A dataset that already says `prompt`/`completion` needs no code — a
    generated cell renaming a column to itself is noise that reads as a bug."""
    from backend.modules.datasets import formats

    rows = [{"prompt": "q", "completion": "a"} for _ in range(3)]
    detection = formats.detect(["prompt", "completion"], rows)
    adaptation = formats.adapt(detection, "sft")
    assert detection.format == "prompt_completion"
    assert not adaptation.needs_formatting
    assert formats.formatting_source(detection.format, adaptation.columns) == ""


def test_prompt_completion_still_cannot_train_dpo():
    """Widening the detector must not widen what the tasks accept: the refusal is
    the feature."""
    from backend.modules.datasets import formats

    detection = formats.detect(["question", "solution", "answer"], _limo_rows())
    adaptation = formats.adapt(detection, "dpo")
    assert not adaptation.ok
    assert "preference" in adaptation.problem


def test_alpaca_still_wins_over_prompt_completion():
    """Order matters: a real Alpaca set has `instruction` AND `output`, and both
    branches can claim it. Alpaca is checked first and keeps it, because its
    reshape carries `input` as context and the rename would drop that column."""
    from backend.modules.datasets import formats

    rows = [
        {"instruction": "Translate", "input": "hola", "output": "hello"}
        for _ in range(3)
    ]
    detection = formats.detect(["instruction", "input", "output"], rows)
    assert detection.format == "alpaca"
