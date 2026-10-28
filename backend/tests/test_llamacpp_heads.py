"""Head scoring (`llamacpp/heads.py`).

Synthetic attention patterns pin each score to the head shape it is meant to find:
a head that always looks one back is a previous-token head and nothing else; a
head that looks at the token after the earlier copy is an induction head. The
three-state rule is pinned too — a prompt that repeats nothing makes induction
*unmeasurable*, and that must come back as None, not 0.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.modules.llamacpp import heads, traces

# BOS, then A B C D, then A B C D again.
TOKENS = [1, 10, 11, 12, 13, 10, 11, 12, 13]
N = len(TOKENS)


def one_hot_rows(target: list[int]) -> np.ndarray:
    """A [1, N, N] head where query q puts all its weight on `target[q]`."""
    probs = np.zeros((1, N, N))
    for q, t in enumerate(target):
        probs[0, q, t] = 1.0
    return probs


def test_previous_token_head() -> None:
    probs = one_hot_rows([0] + list(range(N - 1)))
    [score] = heads.score_record(probs, 0, TOKENS, layer=3)
    # Averaged over the queries that *have* a previous position — BOS has none.
    assert score.prevToken == pytest.approx(1.0)
    assert score.layer == 3 and score.rows == N
    assert score.induction is not None and score.induction < 0.3


def test_induction_head() -> None:
    # In the second half, query q (token X) looks at the token that followed the
    # earlier X: q - 4 + 1. Elsewhere it sits on BOS.
    target = [0] * N
    for q in range(5, N):
        target[q] = q - 3
    [score] = heads.score_record(one_hot_rows(target), 0, TOKENS, layer=0)
    assert score.induction == pytest.approx(1.0)
    assert score.duplicate == pytest.approx(0.0)


def test_duplicate_head() -> None:
    target = [0] * N
    for q in range(5, N):
        target[q] = q - 4
    [score] = heads.score_record(one_hot_rows(target), 0, TOKENS, layer=0)
    assert score.duplicate == pytest.approx(1.0)
    assert score.induction == pytest.approx(0.0)


def test_no_repeats_means_unmeasurable_not_zero() -> None:
    tokens = [1, 10, 11, 12]
    probs = np.full((1, 4, 4), 0.25)
    [score] = heads.score_record(probs, 0, tokens, layer=0)
    assert score.induction is None and score.duplicate is None


def test_a_pruned_record_scores_only_the_rows_it_holds() -> None:
    # The last block of a prompt pass often keeps only the final query row.
    probs = np.zeros((1, 1, N))
    probs[0, 0, N - 2] = 1.0
    [score] = heads.score_record(probs, N - 1, TOKENS, layer=27)
    assert score.rows == 1
    assert score.prevToken == pytest.approx(1.0)


def test_the_probe_repeats_its_sequence() -> None:
    words = heads.probe_prompt(12, seed=3).split()
    assert len(words) == 24 and words[:12] == words[12:]
    assert len(set(words[:12])) == 12
    assert heads.probe_prompt(12, seed=3) == heads.probe_prompt(12, seed=3)


def test_a_trace_without_attention_says_so(tmp_path: Path) -> None:
    trace = traces.Trace(trace_id="t", directory=tmp_path, manifest={"records": []})
    assert "attention" in heads.head_table(trace)["error"]


#: The developer's own traces. The test environment points the data directory at a
#: temp folder, so `traces.traces_root()` is empty here by design.
REPO_TRACES = Path(__file__).resolve().parents[2] / ".data" / "llamacpp" / "traces"


def _real_attention_traces() -> list[traces.Trace]:
    import json

    found = []
    for directory in sorted(REPO_TRACES.glob("*")) if REPO_TRACES.is_dir() else []:
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("attention"):
            found.append(traces.Trace(directory.name, directory, manifest))
    return found


def test_a_real_trace_on_disk_scores_every_head() -> None:
    found = _real_attention_traces()
    if not found:
        pytest.skip("no attention trace on this machine")
    table = heads.head_table(found[0])
    assert (
        table["heads"]
        and len(table["heads"]) == table["layers"] * table["headsPerLayer"]
    )
    for h in table["heads"]:
        assert 0.0 <= h["prevToken"] <= 1.0 + 1e-6
        assert h["entropy"] >= 0.0
        if table["repeatedPositions"] == 0:
            assert h["induction"] is None


def test_a_real_probe_finds_induction_heads() -> None:
    # The probe on Llama 3.2 1B: a handful of heads score near 1, the median near 0.
    probes = [
        t
        for t in _real_attention_traces()
        if (t.manifest.get("prompt") or "") == heads.probe_prompt(24, 0)
    ]
    if not probes:
        pytest.skip("no induction-probe trace on this machine")
    table = heads.head_table(probes[0])
    scores = sorted(
        h["induction"] for h in table["heads"] if h["induction"] is not None
    )
    assert table["repeatedPositions"] > 0
    assert scores[-1] > 0.5, "no head copies the continuation"
    assert scores[len(scores) // 2] < 0.1, "induction should be rare, not everywhere"
