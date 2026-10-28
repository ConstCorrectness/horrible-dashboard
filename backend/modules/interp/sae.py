"""Sparse autoencoders on llama.cpp traces: which features a prompt lit up, and
whether the SAE can be trusted on these activations at all.

An SAE is a dictionary for one site of one model: Gemma Scope 2's
`resid_post/layer_13_*` rewrites the residual stream after block 13 of Gemma 3 1B
IT as a sum of a few of its 16 384 directions. Here it is applied in numpy to the
residuals a trace already holds — `l_out` is llama.cpp's name for exactly that
site (the block's output, after its post-norms and residual add) — so features
cost no torch and no GPU.

**The self-check is the point.** Gemma Scope was trained on bf16 activations of
the Hugging Face model; a trace holds the activations of a quantized GGUF run by
llama.cpp. Close is not the same, and an SAE fed activations it was not trained on
still returns features — confident-looking, and wrong. So every encoding reports
how well the SAE *reconstructs* what it was given:

- **FVU** (fraction of variance unexplained), `Σ‖x − x̂‖² / Σ‖x − x̄‖²` over the
  tokens, and the mean cosine between `x` and `x̂`.
- Position 0 is left out. Gemma's BOS residual is an outlier hundreds of times the
  norm of the rest (the attention sink), SAEs are trained without it, and one
  unexplained BOS would read as a failed SAE.
- **L0**, features active per token, beside the L0 the SAE was trained to (its
  config states it). A wrong site or a scale error moves L0 long before it moves
  anything a reader would notice.
- `verified` when FVU ≤ `FVU_OK`, `drifted` above it, `not_checked` when there are
  too few tokens to say. The features are shown either way, marked — the reader
  decides, knowing.

The strongest check is per feature, `label_check`: trace Neuronpedia's own top
example for a feature through this GGUF and compare the activation at its peak
token with Neuronpedia's (computed on the bf16 Hugging Face model). It sends
nothing of the user's — the text is Neuronpedia's.

Architectures: JumpReLU (Gemma Scope), ReLU, TopK — read from the config's
`architecture`, never assumed, with the parameter names asserted on load.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: FVU at or under this is `verified`. Measured on Gemma 3 1B IT Q8_0 with Gemma
#: Scope 2 `layer_13_width_16k_l0_medium`: FVU 0.31, cosine 0.995, L0 60.0 (config:
#: 60) — and per-feature peaks within ~1% of Neuronpedia's bf16 values for 7 of 8
#: features checked. fp16 and f32 trace storage gave the same FVU. 0.4 leaves room
#: for prompts unlike the training mix without passing a mismatched site or model.
FVU_OK = 0.4

#: Features kept per token, and overall.
TOP_PER_TOKEN = 8
TOP_OVERALL = 40

#: SAEs held in memory — ~150 MB each for a 16k × 1152 dictionary in f32.
_CACHE_SIZE = 2

#: Which trace node an SAE site reads. Only sites a trace actually captures.
SITE_NODE = {"resid_post": "l_out"}

_HOOK_LAYER = re.compile(r"(?:layers|blocks)\.(\d+)\.")


class SaeError(Exception):
    """The SAE cannot be applied here — a reason for the reader, not a crash."""


@dataclass
class Sae:
    artifact_id: str
    arch: str  # jump_relu | relu | topk
    site: str  # resid_post
    layer: int
    w_enc: np.ndarray  # [d_in, width]
    b_enc: np.ndarray  # [width]
    w_dec: np.ndarray  # [width, d_in]
    b_dec: np.ndarray  # [d_in]
    threshold: np.ndarray | None  # [width], JumpReLU only
    k: int | None  # TopK only
    #: The mean L0 the SAE was trained to, when its config states it.
    expected_l0: float | None = None

    @property
    def d_in(self) -> int:
        return int(self.w_enc.shape[0])

    @property
    def width(self) -> int:
        return int(self.w_enc.shape[1])

    def encode(self, x: np.ndarray) -> np.ndarray:
        """`[n, d_in]` → `[n, width]` feature activations."""
        pre = x @ self.w_enc + self.b_enc
        if self.arch == "jump_relu":
            assert self.threshold is not None
            return np.where(pre > self.threshold, pre, 0.0).astype(np.float32)
        if self.arch == "topk":
            k = int(self.k or 0)
            out = np.zeros_like(pre)
            idx = np.argpartition(-pre, k - 1, axis=1)[:, :k]
            rows = np.arange(pre.shape[0])[:, None]
            out[rows, idx] = np.maximum(pre[rows, idx], 0.0)
            return out
        return np.maximum(pre, 0.0)

    def decode(self, acts: np.ndarray) -> np.ndarray:
        return acts @ self.w_dec + self.b_dec


# ── loading ──────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_cache: OrderedDict[str, Sae] = OrderedDict()


def _first(params: dict[str, np.ndarray], *names: str) -> np.ndarray | None:
    return next((params[n] for n in names if n in params), None)


def _site_and_layer(folder: Path, config: dict[str, Any]) -> tuple[str, int]:
    hook = str(config.get("hf_hook_point_in") or config.get("hook_name") or "")
    m = _HOOK_LAYER.search(hook)
    layer = int(m.group(1)) if m else None
    if layer is None and (m := re.search(r"layer_(\d+)", folder.name)):
        layer = int(m.group(1))
    if layer is None:
        raise SaeError(f"{folder.name}: the config names no layer")
    # Gemma Scope keeps the site as the folder above the SAE; SAELens names a hook.
    site = folder.parent.name if folder.parent.name in SITE_NODE else ""
    if not site and ("resid_post" in hook or hook.endswith(".output")):
        site = "resid_post"
    return site, layer


def load(artifact: dict[str, Any]) -> Sae:
    """The SAE of a Library artifact, from its `params.safetensors` + `config.json`."""
    with _lock:
        if artifact["id"] in _cache:
            _cache.move_to_end(artifact["id"])
            return _cache[artifact["id"]]
    folder = Path(artifact["path"])
    params_path, config_path = folder / "params.safetensors", folder / "config.json"
    if not params_path.is_file() or not config_path.is_file():
        raise SaeError(
            f"{artifact['id']} has no params.safetensors + config.json — "
            "only Gemma Scope–style SAE folders load here"
        )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    from safetensors.numpy import load_file

    params = {
        k: v.astype(np.float32, copy=False) for k, v in load_file(params_path).items()
    }
    w_enc = _first(params, "w_enc", "W_enc")
    w_dec = _first(params, "w_dec", "W_dec")
    b_enc = _first(params, "b_enc")
    b_dec = _first(params, "b_dec")
    if w_enc is None or w_dec is None or b_enc is None or b_dec is None:
        raise SaeError(
            f"{artifact['id']}: expected w_enc/w_dec/b_enc/b_dec, found {sorted(params)}"
        )
    arch = str(config.get("architecture") or "").lower().replace("-", "_")
    arch = {"jumprelu": "jump_relu", "standard": "relu"}.get(arch, arch)
    threshold = _first(params, "threshold")
    if arch == "jump_relu" and threshold is None:
        raise SaeError(f"{artifact['id']}: a JumpReLU SAE without a threshold")
    if arch not in ("jump_relu", "relu", "topk"):
        raise SaeError(f"{artifact['id']}: unsupported SAE architecture {arch!r}")
    if w_enc.shape != (w_dec.shape[1], w_dec.shape[0]):
        raise SaeError(
            f"{artifact['id']}: encoder {w_enc.shape} and decoder {w_dec.shape} disagree"
        )
    site, layer = _site_and_layer(folder, config)
    sae = Sae(
        artifact_id=artifact["id"],
        arch=arch,
        site=site,
        layer=layer,
        w_enc=w_enc,
        b_enc=b_enc,
        w_dec=w_dec,
        b_dec=b_dec,
        threshold=threshold,
        k=int(config["k"]) if arch == "topk" and "k" in config else None,
        expected_l0=float(config["l0"])
        if isinstance(config.get("l0"), (int, float))
        else None,
    )
    with _lock:
        _cache[artifact["id"]] = sae
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return sae


# ── on a trace ───────────────────────────────────────────────────────────────


def reconstruction_check(x: np.ndarray, x_hat: np.ndarray, skip: int) -> dict[str, Any]:
    """FVU and cosine over the rows from `skip` on: how much of what the SAE was
    given it can explain."""
    x, x_hat = x[skip:], x_hat[skip:]
    if x.shape[0] < 2:
        return {
            "state": "not_checked",
            "reason": "fewer than two tokens after BOS — variance is undefined",
            "tokens": int(x.shape[0]),
        }
    err = float(((x - x_hat) ** 2).sum())
    var = float(((x - x.mean(axis=0)) ** 2).sum())
    norms = np.linalg.norm(x, axis=1) * np.linalg.norm(x_hat, axis=1)
    cos = float(np.mean((x * x_hat).sum(axis=1) / np.maximum(norms, 1e-12)))
    fvu = err / var if var > 0 else float("inf")
    return {
        "state": "verified" if fvu <= FVU_OK else "drifted",
        "fvu": round(fvu, 4),
        "cosine": round(cos, 4),
        "threshold": FVU_OK,
        "tokens": int(x.shape[0]),
        "skipped": skip,
    }


def trace_tokens(trace: Any) -> list[dict[str, Any]]:
    """The tokens a trace ran on (`tokens.json`), or none if it is unreadable."""
    try:
        rows = json.loads((trace.directory / "tokens.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def encode_trace(trace: Any, sae: Sae, pass_index: int = 0) -> dict[str, Any]:
    """Every token's top features at the SAE's site, the trace's top features, and
    the reconstruction check. Raises `SaeError` with a reason when it cannot apply."""
    from backend.modules.llamacpp import lens

    node = SITE_NODE.get(sae.site)
    if node is None:
        raise SaeError(
            f"site {sae.site or '?'} is not one a trace captures (only resid_post → l_out)"
        )
    try:
        residuals = lens.read_residuals(trace, pass_index)
    except lens.LensError as exc:
        raise SaeError(str(exc)) from exc
    matrix = residuals.by_layer.get(sae.layer)
    if matrix is None:
        raise SaeError(
            f"the trace has no l_out for layer {sae.layer} — it was recorded without it, "
            "or the model has fewer layers"
        )
    if residuals.n_embd != sae.d_in:
        raise SaeError(
            f"the trace's residual is {residuals.n_embd} wide and the SAE reads {sae.d_in}: "
            "a different model"
        )
    x = np.ascontiguousarray(
        matrix.T, dtype=np.float32
    )  # [k, d] — the last k positions
    first = residuals.n_tokens - x.shape[0]
    if not np.isfinite(x).all():
        raise SaeError(
            "the trace holds non-finite residuals at this layer (an fp16 overflow?) — "
            "record it at full fidelity"
        )
    acts = sae.encode(x)
    check = reconstruction_check(x, sae.decode(acts), skip=1 if first == 0 else 0)
    check["l0"] = (
        round(float((acts[1 if first == 0 else 0 :] > 0).sum(axis=1).mean()), 1)
        if acts.shape[0] > 1
        else None
    )
    check["expectedL0"] = sae.expected_l0

    tokens = trace_tokens(trace)
    per_token = []
    for row in range(acts.shape[0]):
        a = acts[row]
        nz = np.flatnonzero(a)
        top = nz[np.argsort(-a[nz])][:TOP_PER_TOKEN]
        per_token.append(
            {
                "position": first + row,
                "active": int(nz.size),
                "top": [{"index": int(i), "act": round(float(a[i]), 3)} for i in top],
            }
        )
    # Overall: by peak activation, skipping BOS, whose features say "BOS".
    body = acts[1:] if first == 0 and acts.shape[0] > 1 else acts
    peak = body.max(axis=0)
    order = np.argsort(-peak)[:TOP_OVERALL]
    offset = first + (1 if first == 0 and acts.shape[0] > 1 else 0)
    overall = [
        {
            "index": int(i),
            "peak": round(float(peak[i]), 3),
            "position": offset + int(body[:, i].argmax()),
            "tokens": int((body[:, i] > 0).sum()),
            # Peaks on the first token after BOS: real (that residual is not an
            # outlier, unlike BOS's), but Neuronpedia's examples never show it, so
            # a label read there is a label for a different context.
            "firstToken": first == 0 and int(body[:, i].argmax()) == 0,
        }
        for i in order
        if peak[i] > 0
    ]
    return {
        "artifactId": sae.artifact_id,
        "arch": sae.arch,
        "site": sae.site,
        "layer": sae.layer,
        "width": sae.width,
        "fidelity": trace.manifest.get("fidelity"),
        "tokens": [
            {
                "position": int(t.get("index", i)),
                "text": t.get("text", ""),
                "generated": bool(t.get("generated")),
            }
            for i, t in enumerate(tokens)
        ],
        "firstPosition": first,
        "perToken": per_token,
        "top": overall,
        "check": check,
    }


def feature_activations(
    trace: Any, sae: Sae, index: int, pass_index: int = 0
) -> dict[str, Any]:
    """One feature's activation at every position of a trace."""
    from backend.modules.llamacpp import lens

    if not 0 <= index < sae.width:
        raise SaeError(f"feature {index} is out of range (width {sae.width})")
    residuals = lens.read_residuals(trace, pass_index)
    matrix = residuals.by_layer.get(sae.layer)
    if matrix is None:
        raise SaeError(f"the trace has no l_out for layer {sae.layer}")
    x = np.ascontiguousarray(matrix.T, dtype=np.float32)
    pre = x @ sae.w_enc[:, index] + sae.b_enc[index]
    if sae.arch == "jump_relu":
        assert sae.threshold is not None
        values = np.where(pre > sae.threshold[index], pre, 0.0)
    elif sae.arch == "topk":
        values = sae.encode(x)[:, index]  # TopK needs the whole row to know
    else:
        values = np.maximum(pre, 0.0)
    first = residuals.n_tokens - x.shape[0]
    return {
        "index": index,
        "firstPosition": first,
        "values": [round(float(v), 3) for v in values],
        "threshold": None
        if sae.threshold is None
        else round(float(sae.threshold[index]), 3),
    }


# ── against Neuronpedia ──────────────────────────────────────────────────────


async def label_check(
    artifact: dict[str, Any], index: int, np_model: str, np_source: str, model_path: str
) -> dict[str, Any]:
    """Does feature `index` here mean what Neuronpedia's label says?

    Traces Neuronpedia's own top example through `model_path` and compares this
    SAE's activation at the example's peak token with Neuronpedia's (bf16 HF)
    value. Sends nothing of the user's: the text is Neuronpedia's. The trace is
    deleted afterwards — it is evidence for one number, not a trace to keep.
    """
    from backend.modules.interp import neuronpedia
    from backend.modules.llamacpp import trace_runner, traces
    from backend.modules.llamacpp.routes import _architecture

    sae = load(artifact)
    body, _ = await neuronpedia.get_json(
        f"/api/feature/{np_model}/{np_source}/{int(index)}"
    )
    examples = (body or {}).get("activations") or []
    if not examples:
        return {
            "state": "not_checked",
            "reason": "Neuronpedia has no examples for this feature",
        }
    ex = examples[0]
    expected = float(ex.get("maxValue") or 0.0)
    peak = int(ex.get("maxValueTokenIndex") or 0)
    tokens = [str(t) for t in ex.get("tokens") or []]
    text = "".join(tokens).replace("▁", " ")
    if text.startswith("<bos>"):
        text = text[len("<bos>") :]  # the tracer adds its own
    spec = {
        "modelPath": model_path,
        "prompt": text,
        "tokenIds": None,
        "capture": ["l_out"],
        "derivedFrom": "",
        "edits": [],
        "maxTokens": 0,
        "layers": [sae.layer],
        "attention": False,
        "fidelity": "fp16",
        "tokenCap": 1024,
        "gpuLayers": 0,
        "architecture": _architecture(model_path),
    }
    trace_id: str | None = None
    error: str | None = None
    async for event in trace_runner.run_trace(spec):
        trace_id = event.get("traceId", trace_id)
        error = event.get("error") or error
    if not trace_id or error:
        return {"state": "failed", "reason": error or "the trace did not finish"}
    try:
        trace = traces.load(trace_id)
        if trace is None:
            return {"state": "failed", "reason": "the trace was not written"}
        values = feature_activations(trace, sae, index)["values"]
        ours_tokens = len(trace_tokens(trace))
    finally:
        traces.delete_trace(trace_id)
    at_peak = values[peak] if peak < len(values) else None
    aligned = ours_tokens == len(tokens)
    if at_peak is None or not aligned:
        return {
            "state": "not_checked",
            "reason": (
                f"this tokenizer split Neuronpedia's text into {ours_tokens} tokens, not "
                f"{len(tokens)} — positions do not line up"
            ),
            "expected": expected,
            "ours": max(values[1:] or [0.0]),
        }
    ratio = at_peak / expected if expected else None
    return {
        "state": "verified"
        if ratio is not None and 0.8 <= ratio <= 1.25
        else "drifted",
        "expected": round(expected, 3),
        "ours": round(at_peak, 3),
        "ratio": None if ratio is None else round(ratio, 3),
        "token": tokens[peak].replace("▁", " ") if peak < len(tokens) else None,
        "tolerance": [0.8, 1.25],
    }
