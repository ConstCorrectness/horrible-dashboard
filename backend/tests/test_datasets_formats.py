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
