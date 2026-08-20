"""Authoring a benchmark block: the peek, the presets, and the comparison preview.

The preview is the reason this surface exists. Every benchmark case this module has
got wrong was wrong the same way — the template named a column the dataset does not
have, or the answer column held the worked solution rather than the answer — and
both look exactly like a bad model until ten minutes of run time have been spent.

The Hub is not called here. `first_rows` needs the network, and a test that needs
the network is a test that does not run; what is worth pinning is what the preview
concludes from a row, which is pure.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.evals import datasets, harness
from backend.modules.evals.presets import PRESETS

# A real GSM8K row, trimmed. The whole point is that the answer column carries the
# worked solution and marks the answer with `####`.
GSM8K_ROW = {
    "question": "Janet's ducks lay 16 eggs per day...",
    "answer": (
        "Janet sells 16 - 3 - 4 = <<16-3-4=9>>9 duck eggs a day.\n"
        "She makes 9 * 2 = $<<9*2=18>>18 every day at the farmer's market.\n#### 18"
    ),
}


@pytest.fixture
def client():
    return TestClient(app)


def preview(**kwargs):
    body = {
        "input_template": "{question}",
        "target_column": "answer",
        "target_regex": "",
        "prediction_regex": "",
        "sample_prediction": "",
    }
    body.update(kwargs)
    return datasets.compare_preview(GSM8K_ROW, **body)


# --- the preview catches what a run would only reveal in ten minutes ---------


def test_the_zero_score_case_is_warned_about_before_the_run():
    """The original failure: no regex at all, so the reference is the whole worked
    solution. There is no failed match to complain about, which is exactly why an
    earlier version of this check missed it."""
    problems = preview()["problems"]
    assert any("no target_regex" in p for p in problems)
    # And it names the fix rather than just the fault.
    assert any("####" in p for p in problems)


def test_a_regex_that_does_not_match_is_warned_about():
    """`extract` degrades to "compare the whole thing", which is recoverable but
    almost always a mistake."""
    problems = preview(target_regex=r"ANSWER:\s*(.+)")["problems"]
    assert any("did not match" in p for p in problems)


def test_a_correct_case_has_no_problems():
    out = preview(
        target_regex=r"####\s*(.+)",
        prediction_regex=r"(-?[\d.,]+)[^\d]*$",
        sample_prediction="The answer is 18.",
    )
    assert out["problems"] == []
    assert out["reference"] == "18"
    assert out["reference_normalised"] == "18"
    assert out["prediction_normalised"] == "18"


def test_a_template_naming_a_missing_column_is_caught():
    problems = preview(input_template="{nonsense}")["problems"]
    assert any("nonsense" in p and "does not have" in p for p in problems)
    # And it lists what IS there, so the fix does not need another round trip.
    assert any("question, answer" in p for p in problems)


def test_a_missing_target_column_is_caught():
    problems = preview(target_column="label")["problems"]
    assert any("'label' is not in this dataset" in p for p in problems)


def test_a_short_single_line_reference_is_not_nagged_about():
    """The warning has to be quiet when the answer column really does hold just the
    answer, or it becomes noise people learn to ignore."""
    out = datasets.compare_preview(
        {"q": "2+2?", "a": "4"},
        input_template="{q}",
        target_column="a",
        target_regex="",
        prediction_regex="",
    )
    assert out["problems"] == []


# --- the preview uses the harness's own scoring ------------------------------


def test_the_preview_cannot_disagree_with_the_harness():
    """`extract` and `normalise` are imported, not reimplemented. A preview with its
    own copy could flatter a case that the real run then scores zero — and the thing
    the two would disagree about is what the score means."""
    assert datasets.harness.extract is harness.extract
    assert datasets.harness.normalise is harness.normalise

    # And the generated script carries the same source, so all three agree.
    job = {
        "dataset": "d",
        "config": "",
        "split": "test",
        "input_template": "{q}",
        "target_column": "a",
        "target_regex": "",
        "prediction_regex": "",
        "metric": "contains",
        "limit": 1,
        "system": "",
        "endpoint": "http://x",
        "model": "m",
        "timeout": 1,
    }
    source = harness.render(job)
    assert "def extract(" in source
    assert "def normalise(" in source


# --- presets ------------------------------------------------------------------


def test_every_preset_is_a_valid_benchmark_block():
    """A preset that does not validate would fail at save time, in a form, with a
    422 the user did not cause."""
    from backend.modules.evals.models import HfBenchmark

    for preset in PRESETS:
        HfBenchmark(**{**HfBenchmark(dataset="x").model_dump(), **preset["benchmark"]})


def test_the_gsm8k_preset_actually_fixes_the_gsm8k_row():
    """The preset is the lesson written down; this asserts the lesson is right."""
    gsm8k = next(p for p in PRESETS if p["id"] == "gsm8k")["benchmark"]
    out = preview(
        target_regex=gsm8k["target_regex"],
        prediction_regex=gsm8k["prediction_regex"],
        sample_prediction="...so she makes $18.",
    )
    assert out["problems"] == []
    assert out["reference_normalised"] == "18"
    assert out["prediction_normalised"] == "18"


def test_every_preset_explains_itself():
    """`why` is what the form shows on hover. A preset nobody understands is a
    preset people override wrongly."""
    for preset in PRESETS:
        assert preset["why"].strip()
        assert preset["label"].strip()


# --- routes -------------------------------------------------------------------


def test_the_presets_route_serves_them(client):
    body = client.get("/api/evals/datasets/presets").json()
    assert {p["id"] for p in body["presets"]} >= {"gsm8k", "mmlu"}


def test_the_compare_preview_route_returns_the_problems(client):
    body = client.post(
        "/api/evals/datasets/compare-preview",
        json={
            "row": GSM8K_ROW,
            "input_template": "{question}",
            "target_column": "answer",
        },
    ).json()
    assert any("no target_regex" in p for p in body["problems"])
    assert body["reference_raw"].endswith("#### 18")
