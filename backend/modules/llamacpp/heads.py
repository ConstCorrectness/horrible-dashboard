"""Every attention head of a traced pass, scored — HeadVis, for a local model.

The stepper shows one head's matrix at a time. This answers the question before
it: *which* of a model's few hundred heads is worth looking at. Each head of the
prompt pass gets the scores interpretability work uses to find known circuits:

- **previous-token** — mean weight a query puts on the position just before it.
  The first half of an induction circuit.
- **induction** — mean weight a query puts on the token that *followed* an
  earlier occurrence of the current token: "last time A was followed by B, so
  look at B". The head that copies a continuation forward.
- **duplicate** — mean weight on earlier occurrences of the current token itself.
- **sink**, **self**, **entropy** — the stepper's `head_summaries`: weight on
  position 0, on the diagonal, and how spread the head is (nats).

Induction and duplicate are only *measurable* on a prompt that repeats something.
"The capital of France is" repeats nothing, so on it they are reported as not
measurable — never as 0.0, which would read as "this model has no induction
heads". `probe_prompt` builds a prompt that does: a random word sequence, twice.

Scores are means over the query rows the record holds. The last block of a
prompt pass is often pruned to the final token, so `rows` is reported per head
and a one-row score is a thin one.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from backend.modules.llamacpp import stepper, traces

#: Common, single-meaning words: each tends to be one token with a leading space
#: across tokenizers, so the two halves of the probe tokenize alike.
_PROBE_WORDS = (
    "river stone apple garden window silver orange planet copper meadow "
    "lantern violet harbor pencil thunder marble canyon feather rocket "
    "candle forest bridge ladder pepper glacier saddle compass velvet "
    "anchor cotton falcon tunnel walnut quartz basket meteor island "
    "cabin needle orchid ribbon summit timber wagon zebra"
).split()


def probe_prompt(words: int = 24, seed: int = 0) -> str:
    """A random word sequence, then the same sequence again.

    Random so no continuation is predictable from meaning: in the second half
    the only way to predict the next word is to find where the current one
    appeared before and copy what followed — exactly what an induction head does.
    """
    rng = random.Random(seed)
    sequence = rng.sample(_PROBE_WORDS, k=min(words, len(_PROBE_WORDS)))
    return " ".join(sequence + sequence)


@dataclass
class HeadScore:
    layer: int
    head: int
    entropy: float
    selfWeight: float
    sink: float
    prevToken: float
    #: None when the prompt repeats nothing this head's rows could test.
    induction: float | None
    duplicate: float | None
    #: Query rows the score averages over.
    rows: int


def _targets(token_ids: list[int], q: int) -> tuple[list[int], list[int]]:
    """For query position `q`: earlier positions holding the same token
    (duplicate targets), and the positions right after them (induction targets).

    Position 0 (the BOS token on most models) is never a match: every sequence
    starts with it, and matching it would credit the attention sink.
    """
    current = token_ids[q]
    duplicates = [j for j in range(1, q) if token_ids[j] == current]
    induction = [j + 1 for j in duplicates if j + 1 < q]
    return duplicates, induction


def score_record(
    probs_by_head: np.ndarray, base: int, token_ids: list[int], layer: int
) -> list[HeadScore]:
    """Score every head of one attention record.

    `probs_by_head` is `[head, row, kv]`, softmax weights over the KV positions in
    use; query row `r` sits at absolute position `base + r`.
    """
    n_heads, n_rows, n_kv = probs_by_head.shape
    out: list[HeadScore] = []
    for h in range(n_heads):
        probs = np.clip(probs_by_head[h].astype(np.float64), 0.0, None)
        sums = probs.sum(axis=1, keepdims=True)
        probs = np.divide(probs, sums, out=np.zeros_like(probs), where=sums > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            ent = -(np.where(probs > 0, probs * np.log(probs), 0.0)).sum(axis=1)

        prev, selfw, ind, dup = [], [], [], []
        for r in range(n_rows):
            q = base + r
            if not 0 <= q < n_kv:
                continue
            selfw.append(probs[r, q])
            if q >= 1:
                prev.append(probs[r, q - 1])
            if q < len(token_ids):
                duplicates, induction = _targets(token_ids, q)
                if duplicates:
                    dup.append(probs[r, duplicates].sum())
                if induction:
                    ind.append(probs[r, induction].sum())
        out.append(
            HeadScore(
                layer=layer,
                head=h,
                entropy=float(ent.mean()) if ent.size else 0.0,
                selfWeight=float(np.mean(selfw)) if selfw else 0.0,
                sink=float(probs[:, 0].mean()) if n_kv else 0.0,
                prevToken=float(np.mean(prev)) if prev else 0.0,
                induction=float(np.mean(ind)) if ind else None,
                duplicate=float(np.mean(dup)) if dup else None,
                rows=n_rows,
            )
        )
    return out


def _token_ids(trace: traces.Trace) -> list[int]:
    import json

    path = trace.directory / "tokens.json"
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [int(t["id"]) for t in rows if not t.get("generated")]


def head_table(trace: traces.Trace, pass_index: int = 0) -> dict[str, Any]:
    """Every head of every captured layer of one pass, scored."""
    token_ids = _token_ids(trace)
    records = [
        r
        for r in trace.records
        if r.pass_index == pass_index
        and r.layer is not None
        and r.name.startswith("kq_soft_max")
        and r.fidelity != "summary"
    ]
    if not records:
        return {
            "error": (
                "this trace has no attention weights — record it with attention "
                "capture on (the heads probe does)"
            )
        }
    heads: list[HeadScore] = []
    for record in sorted(records, key=lambda r: r.layer or 0):
        array = stepper.record_array(trace, record)[0]  # [head, row, kv]
        used = stepper.kv_in_use(trace, record.pass_index, array.shape[2])
        array = array[:, :, :used]
        base = used - array.shape[1]
        heads.extend(score_record(array, base, token_ids, int(record.layer or 0)))

    repeats = sum(1 for q in range(len(token_ids)) if _targets(token_ids, q)[1])
    manifest = trace.manifest
    return {
        "traceId": trace.trace_id,
        "model": manifest.get("modelName", ""),
        "modelPath": manifest.get("modelPath", ""),
        "prompt": manifest.get("prompt", ""),
        "tokens": len(token_ids),
        #: Query positions where induction is testable at all. 0 = the prompt
        #: repeats nothing, and induction / duplicate are not measurable.
        "repeatedPositions": repeats,
        "layers": len(records),
        "headsPerLayer": max((h.head for h in heads), default=-1) + 1,
        "heads": [asdict(h) for h in heads],
    }
