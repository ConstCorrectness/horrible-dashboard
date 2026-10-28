"""Steering a GGUF with an SAE feature, and the evidence that it took.

A feature's decoder row is a direction in the residual stream. Adding it, scaled,
after one block (a llama.cpp control vector — `tracer._apply_steer`) pushes every
token toward that feature, and the generation that follows shows what the model
does with the push. Both runs are ordinary traces: the default generation, and a
fork of it (`derivedFrom`, `edits: [{kind: "steer", …}]`) on the same prompt
tokens with the vector added. So the steered run can be read like any trace — the
Features section shows the feature lit up on it.

**Did it take?** Three answers, in the order they can go wrong:

- **arrived** — the feature's own activation at the steered layer, default vs
  steered. A push that the SAE does not read back as that feature is not steering
  *with this feature*, whatever the text does.
- **next token** — how the first prediction's distribution moved: the tokens that
  gained and lost the most log-probability.
- **text** — what each run went on to say, greedily, so the difference is the
  vector's and not the sampler's.

Strength is in units of the feature's own scale: `strength × peak`, where `peak`
is its strongest activation in the default run (or Neuronpedia's maximum when it
did not fire there). A raw scale is meaningless across features — Gemma's
residual norm at layer 13 is ~6 000, and a feature's peak is in the hundreds.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable
from typing import Any

import numpy as np

from backend.modules.interp import sae

#: Tokens each run generates. Greedy, so short is enough to see a difference.
DEFAULT_NEW_TOKENS = 32
MAX_NEW_TOKENS = 128
#: Movers listed for the first prediction.
TOP_MOVERS = 8


class SteerError(Exception):
    """Steering cannot run as asked — a reason for the reader."""


def feature_direction(model: sae.Sae, index: int) -> np.ndarray:
    """The unit direction a feature writes: its decoder row, normalised."""
    if not 0 <= index < model.width:
        raise SteerError(f"feature {index} is out of range (width {model.width})")
    row = model.w_dec[index].astype(np.float64)
    norm = float(np.linalg.norm(row))
    if norm == 0:
        raise SteerError(f"feature {index} has a zero decoder row")
    return (row / norm).astype(np.float32)


def _spec(
    model_path: str,
    layer: int,
    max_tokens: int,
    *,
    prompt: str = "",
    token_ids: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "modelPath": model_path,
        "prompt": prompt,
        "tokenIds": token_ids,
        # The steered layer's residual (to read the feature back) and the logits
        # (to see the prediction move). Nothing else: a pair costs megabytes.
        "capture": ["l_out", "result_output"],
        "derivedFrom": "",
        "edits": [],
        "maxTokens": max_tokens,
        "layers": [layer],
        "attention": False,
        "fidelity": "fp16",
        "tokenCap": 1024,
        "gpuLayers": 0,
    }


async def _trace(spec: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Run one trace, passing its progress on; the last event has `traceId`."""
    from backend.modules.llamacpp import trace_runner
    from backend.modules.llamacpp.routes import _architecture

    spec["architecture"] = _architecture(str(spec["modelPath"]))
    trace_id: str | None = None
    async for event in trace_runner.run_trace(spec):
        trace_id = event.get("traceId", trace_id)
        if event.get("error"):
            raise SteerError(str(event["error"]))
        yield event
    if not trace_id:
        raise SteerError("the trace did not finish")
    yield {"traceId": trace_id, "final": True}


def _peak_for(model: sae.Sae, trace: Any, index: int) -> float:
    values = sae.feature_activations(trace, model, index)["values"]
    body = values[1:] if len(values) > 1 else values  # BOS says "BOS"
    return float(max(body or [0.0]))


async def run(
    artifact: dict[str, Any],
    index: int,
    strength: float,
    *,
    model_path: str,
    prompt: str = "",
    baseline_trace_id: str = "",
    max_tokens: int = DEFAULT_NEW_TOKENS,
    fallback_peak: float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Default and steered generations of one prompt, then the comparison.

    Yields `{phase, ...}` progress and finally `{done: true, result}`. A
    `baseline_trace_id` that is the same experiment — same model and prompt, an
    unedited run that generated `max_tokens` with the SAE's layer recorded — is
    reused as the default, so trying another strength costs one run, not two.
    """
    from backend.modules.llamacpp import traces

    model = sae.load(artifact)
    direction = feature_direction(model, index)
    max_tokens = max(1, min(int(max_tokens), MAX_NEW_TOKENS))

    baseline = traces.load(baseline_trace_id) if baseline_trace_id else None
    if baseline is not None and (
        str(baseline.manifest.get("modelPath")) != model_path
        or int(baseline.manifest.get("maxTokens") or 0) != max_tokens
        or baseline.manifest.get("edits")
        or (prompt.strip() and str(baseline.manifest.get("prompt") or "") != prompt)
        or model.layer not in (baseline.manifest.get("layers") or [model.layer])
    ):
        baseline = None  # not the same experiment: run a fresh default
    if baseline is None:
        if not prompt.strip():
            raise SteerError("no prompt to steer")
        yield {"phase": "default", "status": "starting"}
        async for event in _trace(
            _spec(model_path, model.layer, max_tokens, prompt=prompt)
        ):
            if event.get("final"):
                baseline = traces.load(event["traceId"])
            else:
                yield {"phase": "default", **event}
    assert baseline is not None

    peak = _peak_for(model, baseline, index)
    unit = peak if peak > 0 else float(fallback_peak or 0.0)
    if unit <= 0:
        raise SteerError(
            f"F{index} never fires on this prompt and no reference peak was given — "
            "there is no scale to steer it at"
        )
    scale = float(strength) * unit
    prompt_ids = [
        int(t["id"]) for t in sae.trace_tokens(baseline) if not t.get("generated")
    ]
    spec = _spec(
        model_path,
        model.layer,
        max_tokens,
        prompt=str(baseline.manifest.get("prompt") or prompt),
        token_ids=prompt_ids,
    )
    spec["derivedFrom"] = baseline.trace_id
    spec["edits"] = [
        {
            "kind": "steer",
            "source": "sae",
            "artifactId": artifact["id"],
            "feature": index,
            "layer": model.layer,
            "strength": float(strength),
            "unit": round(unit, 3),
            "scale": round(scale, 3),
        }
    ]
    spec["steer"] = {
        "vector": [float(v) for v in direction],
        "scale": scale,
        "layers": [model.layer, model.layer],
    }
    yield {"phase": "steered", "status": "starting", "scale": scale}
    steered = None
    async for event in _trace(spec):
        if event.get("final"):
            steered = traces.load(event["traceId"])
        else:
            yield {"phase": "steered", **event}
    assert steered is not None
    yield {"done": True, "result": compare(baseline, steered, model, index)}


# ── the comparison ───────────────────────────────────────────────────────────


def _generated_text(trace: Any) -> tuple[str, list[str]]:
    pieces = [
        str(t.get("text", "")) for t in sae.trace_tokens(trace) if t.get("generated")
    ]
    return "".join(pieces), pieces


def _first_logits(trace: Any) -> np.ndarray | None:
    """The logits of the first prediction (pass 0's last column)."""
    from backend.modules.llamacpp import lens

    for record in trace.records:
        if record.pass_index == 0 and record.name.startswith("result_output"):
            matrix = lens._record_matrix(trace, record)
            if matrix is not None and matrix.shape[1]:
                return matrix[:, -1].astype(np.float64)
    return None


def _log_softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max()
    return shifted - math.log(float(np.exp(shifted).sum()))


def next_token_shift(
    default: np.ndarray,
    steered: np.ndarray,
    piece: Callable[[int], str] | None,
    top: int = TOP_MOVERS,
) -> dict[str, Any]:
    """How the first prediction moved: each run's top token and the biggest
    gainers and losers in log-probability."""
    a, b = _log_softmax(default), _log_softmax(steered)
    delta = b - a

    def name(i: int) -> str:
        try:
            return piece(i) if piece else f"#{i}"
        except IndexError:
            return f"#{i}"

    def entry(i: int) -> dict[str, Any]:
        return {
            "id": int(i),
            "token": name(int(i)),
            "default": round(float(math.exp(a[i])), 4),
            "steered": round(float(math.exp(b[i])), 4),
            "deltaLogProb": round(float(delta[i]), 3),
        }

    # Movers among tokens that matter in either run — the vocabulary's long tail
    # moves by large log-ratios at probabilities nobody would ever sample.
    relevant = np.flatnonzero((np.exp(a) > 1e-4) | (np.exp(b) > 1e-4))
    order = relevant[np.argsort(-delta[relevant])]
    return {
        "defaultTop": entry(int(a.argmax())),
        "steeredTop": entry(int(b.argmax())),
        "gained": [entry(int(i)) for i in order[:top]],
        "lost": [entry(int(i)) for i in order[::-1][:top]],
        # KL(steered ‖ default): one number for "how much did the prediction move".
        "kl": round(float((np.exp(b) * (b - a)).sum()), 4),
    }


def arrival_state(default_mean: float, steered_mean: float, strength: float) -> str:
    """Did the feature move the way it was pushed? Up for a positive strength,
    down for a negative one — a negative push that leaves the feature at 0 when it
    was already 0 moved nothing, and says so."""
    if strength > 0:
        return "arrived" if steered_mean > default_mean else "did_not_arrive"
    if strength < 0:
        if default_mean <= 0:
            return "nothing_to_suppress"
        return "arrived" if steered_mean < default_mean else "did_not_arrive"
    return "no_push"


def compare(baseline: Any, steered: Any, model: sae.Sae, index: int) -> dict[str, Any]:
    default_text, default_pieces = _generated_text(baseline)
    steered_text, steered_pieces = _generated_text(steered)

    a = sae.feature_activations(baseline, model, index)["values"]
    b = sae.feature_activations(steered, model, index)["values"]
    n_prompt = int(baseline.manifest.get("promptTokens") or len(a))
    # Prompt positions only, BOS left out: the same tokens in both runs, so any
    # difference is the vector's.
    pa = a[1:n_prompt] or [0.0]
    pb = b[1:n_prompt] or [0.0]
    arrived = {
        "defaultMean": round(float(np.mean(pa)), 2),
        "steeredMean": round(float(np.mean(pb)), 2),
        "defaultActiveShare": round(float(np.mean(np.asarray(pa) > 0)), 3),
        "steeredActiveShare": round(float(np.mean(np.asarray(pb) > 0)), 3),
    }
    arrived["state"] = arrival_state(
        arrived["defaultMean"],
        arrived["steeredMean"],
        float((steered.manifest.get("edits") or [{}])[0].get("strength") or 0.0),
    )

    shift = None
    la, lb = _first_logits(baseline), _first_logits(steered)
    if la is not None and lb is not None and la.shape == lb.shape:
        piece = None
        try:
            from backend.modules.llamacpp import lens

            un = lens.load_unembedding(str(baseline.manifest.get("modelPath")))
            # Rendered on demand: the ~17 tokens shown, not a 262k vocabulary.
            piece = lambda i: lens.render_piece(un.vocab[i], un.tokenizer_model)  # noqa: E731
        except Exception:  # noqa: BLE001 — ids are still an answer without names
            piece = None
        shift = next_token_shift(la, lb, piece)

    edit = (steered.manifest.get("edits") or [{}])[0]
    return {
        "defaultTraceId": baseline.trace_id,
        "steeredTraceId": steered.trace_id,
        "feature": index,
        "layer": model.layer,
        "strength": edit.get("strength"),
        "unit": edit.get("unit"),
        "scale": edit.get("scale"),
        "default": {"text": default_text, "tokens": default_pieces},
        "steered": {"text": steered_text, "tokens": steered_pieces},
        "diverges": next(
            (
                i
                for i, (x, y) in enumerate(
                    zip(default_pieces, steered_pieces, strict=False)
                )
                if x != y
            ),
            None
            if default_pieces == steered_pieces
            else min(len(default_pieces), len(steered_pieces)),
        ),
        "arrived": arrived,
        "nextToken": shift,
    }
