"""NLA scoring, the injection contract and pair discovery — no models needed."""

from __future__ import annotations

import numpy as np
import pytest

from backend.modules.interp import nla

PAIR = nla.PAIRS["qwen2.5-7b-L20"]


def test_score_is_the_critics_direction_mse() -> None:
    rng = np.random.default_rng(0)
    gold = rng.normal(size=PAIR["nEmbd"])
    same = nla.score(gold * 7.0, gold, PAIR["mseScale"])  # scale does not matter
    assert same == {"cos": 1.0, "mse": 0.0}
    other = rng.normal(size=PAIR["nEmbd"])
    s = nla.score(other, gold, PAIR["mseScale"])
    # MSE = 2(1 - cos) once both are at L2 = √d.
    assert s["mse"] == pytest.approx(2 * (1 - s["cos"]), abs=2e-3)
    assert nla.score(np.zeros(4), np.ones(4), 2.0) == {"cos": 0.0, "mse": 2.0}


def test_verdict_bands() -> None:
    assert nla.verdict(None) == "not_checked"
    assert nla.verdict(0.85) == "carried"
    assert nla.verdict(0.6) == "partial"
    assert nla.verdict(0.2) == "drifted"
    # Read above the neutral sentence: 0.55 against a 0.45 floor is noise.
    assert nla.verdict(0.55, 0.45) == "drifted"
    assert nla.verdict(0.85, 0.40) == "carried"


def test_extract_explanation_open_and_closed() -> None:
    assert nla.extract_explanation("x <explanation> a cat </explanation> y") == (
        "a cat",
        True,
    )
    # Cut short: the partial text is still a reading, flagged as unclosed.
    assert nla.extract_explanation("<explanation>a ca") == ("a ca", False)
    assert nla.extract_explanation("no tags") == ("no tags", False)


def test_injection_site_needs_both_neighbours() -> None:
    inj, left, right = (
        PAIR["injectionToken"],
        PAIR["leftNeighbour"],
        PAIR["rightNeighbour"],
    )
    assert nla.injection_site([1, left, inj, right, 2], PAIR) == 2
    # The marker without its neighbours is the character, not the slot.
    with pytest.raises(nla.NlaError):
        nla.injection_site([1, inj, right, 2], PAIR)
    with pytest.raises(nla.NlaError):
        nla.injection_site([left, inj, right, left, inj, right], PAIR)


def test_av_prompt_carries_the_default_system_turn_and_the_marker_once() -> None:
    text = nla.av_prompt(PAIR)
    assert text.startswith(
        "<|im_start|>system\nYou are Qwen, created by Alibaba Cloud."
    )
    assert text.count(PAIR["injectionChar"]) == 1
    assert "<concept>㈎</concept>" in text
    assert text.endswith("<|im_start|>assistant\n")


def test_pick_positions() -> None:
    assert nla.pick_positions(40, None, 3) == [37, 38, 39]
    # Early positions are skipped by default when there is room.
    assert nla.pick_positions(12, None, 5) == [10, 11]
    assert nla.pick_positions(5, None, 3) == [2, 3, 4]
    assert nla.pick_positions(20, [-1, 3, 19], 3) == [19, 3]
    with pytest.raises(nla.NlaError):
        nla.pick_positions(20, [25], 3)


def test_position_flags() -> None:
    assert nla.position_flags(3, 120.0, PAIR) == ["early"]
    assert nla.position_flags(40, 14000.0, PAIR) == ["high_norm"]
    assert nla.position_flags(40, 140.0, PAIR) == []


def _meta(**kw: object) -> dict[str, object]:
    base = {
        "general.architecture": "qwen2",
        "qwen2.embedding_length": 3584,
        "qwen2.block_count": 28,
        "general.name": "Qwen2.5 7B Instruct",
    }
    return {**base, **kw}


def test_classify_tells_the_three_parts_apart() -> None:
    assert nla.classify(_meta(), PAIR) == "base"
    assert (
        nla.classify(
            _meta(**{"general.tags": ["nla"], "general.name": "Actor_Hf"}), PAIR
        )
        == "av"
    )
    ar = _meta(
        **{"qwen2.block_count": 21, "nla.role": "critic", "nla.extraction_layer": 20}
    )
    assert nla.classify(ar, PAIR) == "ar"
    assert nla.classify({**ar, "nla.extraction_layer": 18}, PAIR) is None
    assert nla.classify(_meta(**{"qwen2.embedding_length": 896}), PAIR) is None
    assert (
        nla.classify(_meta(**{"general.name": "Qwen2.5 7B"}), PAIR) is None
    )  # not the instruct


def test_summarize_and_readout_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    rows = [
        {"position": 1, "flags": ["early"], "best": {"cos": 0.9, "nullCos": 0.4}},
        {"position": 2, "flags": [], "best": {"cos": 0.3, "nullCos": 0.4}},
        {"position": 3, "flags": [], "best": None},
    ]
    s = nla.summarize(rows)
    assert s == {
        "positions": 3,
        "meanCos": 0.6,
        "meanNullCos": 0.4,
        "carried": 1,
        "partial": 0,
        "drifted": 1,
        "flagged": 1,
    }
    stored = nla.save_readout(
        {"pairId": "qwen2.5-7b-L20", "positions": rows, "summary": s},
        gold={2: np.arange(4.0)},
    )
    assert nla.load_readout(stored["id"])["summary"]["meanCos"] == 0.6
    assert nla.load_gold(stored["id"], 2).tolist() == [0.0, 1.0, 2.0, 3.0]
    with pytest.raises(nla.NlaError):
        nla.load_gold(stored["id"], 7)
    assert nla.list_readouts()[0]["id"] == stored["id"]
    with pytest.raises(nla.NlaError):
        nla.load_readout("../etc")
