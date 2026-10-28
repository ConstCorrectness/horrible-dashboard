"""Natural Language Autoencoders on local GGUFs: what a pair is, how it is scored.

Fraser-Taliente et al. (2026) train two copies of a chat model as an autoencoder
whose bottleneck is English. The **AV** (activation verbalizer) is handed one
residual-stream vector in place of a token's embedding and writes an explanation;
the **AR** (activation reconstructor) reads that explanation and predicts the
vector back. How close the prediction lands — cosine, or the MSE of the two
vectors scaled to √d, which is `2(1 − cos)` — is the explanation's score, measured
rather than taken on trust.

Everything here runs on llama.cpp (`nla_runner`): the base model is traced for the
activation, the AV GGUF is fed it through `llama_batch.embd`, the AR GGUF is traced
for its layer-K residual and the value head (a d×d matrix stored in the AR GGUF) is
applied in numpy. Nothing is fetched at runtime: the pair's contract — injection
token, its neighbours, the scale the AV was trained at, the prompt templates — is
copied here from the checkpoint's `nla_meta.yaml`, and the runner checks it against
the GGUF's own tokenizer before it injects anything.

What a reading is not: the AV writes the *direction* of an activation (vectors are
rescaled to a fixed norm on the way in, and compared by angle on the way out). The
first ~10 positions and rare very-high-norm positions decode poorly even when
nothing is wrong (the authors' own note); the readout flags both.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np

from backend import paths

#: Known NLA pairs, keyed by id. Facts are from the checkpoints' `nla_meta.yaml`
#: (kitft/nla-qwen2.5-7b-L20-{av,ar}) and the authors' inference code.
PAIRS: dict[str, dict[str, Any]] = {
    "qwen2.5-7b-L20": {
        "id": "qwen2.5-7b-L20",
        "label": "Qwen2.5-7B-Instruct · layer 20",
        "source": "kitft/nla-qwen2.5-7b-L20-av, kitft/nla-qwen2.5-7b-L20-ar",
        "baseRepo": "Qwen/Qwen2.5-7B-Instruct",
        "architecture": "qwen2",
        "nLayer": 28,
        "nEmbd": 3584,
        "layer": 20,
        "arLayers": 21,
        # The AV was trained on vectors at this L2 norm; raw magnitudes are OOD.
        "injectionScale": 150.0,
        "mseScale": 59.86651818838306,
        "injectionChar": "㈎",
        "injectionToken": 149705,
        "leftNeighbour": 29,  # '>'  of <concept>
        "rightNeighbour": 522,  # '</' of </concept>
        # Qwen2.5's HF template writes this system turn when there is none;
        # llama.cpp's built-in chatml does not, so the runner writes it.
        "systemPrompt": "You are Qwen, created by Alibaba Cloud. You are a helpful assistant.",
        "avTemplate": (
            "You are a meticulous AI researcher conducting an important investigation"
            " into activation vectors from a language model. Your overall task is to"
            " describe the semantic content of that activation vector.\n\nWe will pass"
            " the vector enclosed in <concept> tags into your context. You must then"
            " produce an explanation for the vector, enclosed within <explanation>"
            " tags. The explanation consists of 2-3 text snippets describing that"
            " vector.\n\nHere is the vector:\n\n<concept>{injection_char}</concept>"
            "\n\nPlease provide an explanation."
        ),
        "arTemplate": "Summary of the following text: <text>{explanation}</text> <summary>",
        # In-distribution fraction of variance explained (normalised), model card.
        "fveNrm": 0.752,
        # Most layer-20 activations sit at 100-170; early template newlines can
        # reach ~14k. Above this the readout is flagged, not hidden.
        "highNorm": 1000.0,
    }
}

#: Positions this early have seen too little context for layer K to carry much.
EARLY_POSITIONS = 10
#: cos ≥ this reads as "the explanation carries the vector" (the authors' "good
#: decode, typical for clean positions" is 0.9; 0.5 is "mediocre").
GOOD_COS = 0.8
WEAK_COS = 0.5
#: The cos floor is not 0: layer-K residuals share a large common direction, so
#: the AR's reconstruction of *any* text lands ~0.4 from a vector (measured,
#: Qwen2.5-7B L20: neutral sentences 0.38-0.44, other texts' explanations 0.35,
#: matched 0.84). Each run reconstructs this sentence once and reports every
#: position's cos above it; a reading within `MIN_LIFT` of it says nothing.
NULL_EXPLANATION = "A token in the middle of an ordinary English sentence."
MIN_LIFT = 0.15

DEFAULT_MAX_TOKENS = 160
DEFAULT_TEMPERATURE = 1.0

_EXPLANATION = re.compile(r"<explanation>\s*(.*?)\s*(?:</explanation>|$)", re.DOTALL)


class NlaError(Exception):
    """A request the NLA cannot serve (no pair on disk, wrong model, bad input)."""


# ── scoring ──────────────────────────────────────────────────────────────────


def extract_explanation(raw: str) -> tuple[str, bool]:
    """The text inside `<explanation>` tags, and whether the tags closed. An
    unclosed tag is a generation cut short; the partial text is still returned."""
    m = _EXPLANATION.search(raw)
    if m is None:
        return raw.strip(), False
    return m.group(1).strip(), "</explanation>" in raw


def score(pred: np.ndarray, gold: np.ndarray, mse_scale: float) -> dict[str, float]:
    """Cosine and the direction-MSE of the authors' critic: both vectors scaled to
    `mse_scale` (√d), mean squared difference — `2(1 − cos)`, 0 perfect, 2 orthogonal."""
    p = np.asarray(pred, np.float64)
    g = np.asarray(gold, np.float64)
    pn, gn = np.linalg.norm(p), np.linalg.norm(g)
    if pn == 0 or gn == 0:
        return {"cos": 0.0, "mse": 2.0}
    p, g = p / pn * mse_scale, g / gn * mse_scale
    return {
        "cos": round(float(p @ g / (mse_scale * mse_scale)), 4),
        "mse": round(float(((p - g) ** 2).mean()), 4),
    }


def verdict(cos: float | None, null_cos: float | None = None) -> str:
    """`carried` / `partial` / `drifted` / `not_checked` — the three-state rule,
    with the middle split so "half the direction" is not read as either. Within
    `MIN_LIFT` of the neutral sentence's cos is drifted whatever the number."""
    if cos is None:
        return "not_checked"
    if null_cos is not None and cos - null_cos < MIN_LIFT:
        return "drifted"
    if cos >= GOOD_COS:
        return "carried"
    if cos >= WEAK_COS:
        return "partial"
    return "drifted"


def position_flags(position: int, norm: float, pair: dict[str, Any]) -> list[str]:
    flags = []
    if position < EARLY_POSITIONS:
        flags.append("early")
    if norm > float(pair["highNorm"]):
        flags.append("high_norm")
    return flags


def injection_site(tokens: list[int], pair: dict[str, Any]) -> int:
    """The one position the AV's vector goes in: the injection token with the
    sidecar's neighbours on both sides. Anything else is template drift, and an
    injection there would be verbalized as the marker character itself."""
    sites = [
        i
        for i in range(1, len(tokens) - 1)
        if tokens[i] == pair["injectionToken"]
        and tokens[i - 1] == pair["leftNeighbour"]
        and tokens[i + 1] == pair["rightNeighbour"]
    ]
    if len(sites) != 1:
        raise NlaError(
            f"expected one injection site with the pair's neighbours, found {len(sites)}"
            " — the tokenizer or the prompt template does not match the checkpoint"
        )
    return sites[0]


def av_prompt(pair: dict[str, Any]) -> str:
    """The AV's canonical prompt in ChatML, with Qwen's default system turn."""
    content = pair["avTemplate"].format(injection_char=pair["injectionChar"])
    return (
        f"<|im_start|>system\n{pair['systemPrompt']}<|im_end|>\n"
        f"<|im_start|>user\n{content}<|im_end|>\n<|im_start|>assistant\n"
    )


def ar_prompt(pair: dict[str, Any], explanation: str) -> str:
    return pair["arTemplate"].format(explanation=explanation)


def pick_positions(n_tokens: int, requested: list[int] | None, limit: int) -> list[int]:
    """Requested positions (negative counts from the end), or the last `limit`
    that are past the early ones."""
    if requested:
        out = []
        for p in requested:
            q = p if p >= 0 else n_tokens + p
            if not 0 <= q < n_tokens:
                raise NlaError(f"position {p} is outside 0..{n_tokens - 1}")
            if q not in out:
                out.append(q)
        return out
    floor = EARLY_POSITIONS if n_tokens > EARLY_POSITIONS else 0
    return list(range(max(floor, n_tokens - limit), n_tokens))


# ── finding the pair on disk ─────────────────────────────────────────────────


#: (path, size, mtime) → the few header keys `classify` reads. The shared header
#: cache holds four files; a scan of a model folder would evict it every time.
_facts: dict[tuple[str, int, int], dict[str, Any]] = {}
_FACT_KEYS = (
    "general.architecture",
    "general.name",
    "general.tags",
    "nla.role",
    "nla.extraction_layer",
)


def _header(path: str) -> dict[str, Any]:
    from backend.modules.interpretability import gguf

    try:
        st = Path(path).stat()
        key = (path, st.st_size, st.st_mtime_ns)
        if key not in _facts:
            meta = gguf.read_header(path).metadata
            arch = meta.get("general.architecture")
            keep = [*_FACT_KEYS, f"{arch}.embedding_length", f"{arch}.block_count"]
            _facts[key] = {k: meta[k] for k in keep if k in meta}
        return _facts[key]
    except Exception:  # noqa: BLE001 — an unreadable file is simply not a match
        return {}


def classify(meta: dict[str, Any], pair: dict[str, Any]) -> str | None:
    """`av`, `ar`, `base` or None for one GGUF header against a pair.

    The AR conversion names itself (`nla.role`, `nla.extraction_layer`); the AV
    carries the `nla` tag and the full depth; the base is the same architecture
    and size with no NLA marks."""
    arch = meta.get("general.architecture")
    if arch != pair["architecture"]:
        return None
    if int(meta.get(f"{arch}.embedding_length") or 0) != pair["nEmbd"]:
        return None
    blocks = int(meta.get(f"{arch}.block_count") or 0)
    tags = [str(t).lower() for t in (meta.get("general.tags") or [])]
    role = meta.get("nla.role")
    if role in ("critic", "ar"):
        layer = meta.get("nla.extraction_layer")
        return "ar" if layer is None or int(layer) == pair["layer"] else None
    if "nla" in tags:
        return "av" if blocks == pair["nLayer"] else None
    name = str(meta.get("general.name") or "").lower()
    if blocks == pair["nLayer"] and "instruct" in name:
        return "base"
    return None


def find_pair(pair_id: str) -> dict[str, Any]:
    """The pair's facts plus the GGUFs found for each part (paths, or None)."""
    from backend.modules.llamacpp import catalog

    pair = PAIRS.get(pair_id)
    if pair is None:
        raise NlaError(f"no NLA pair {pair_id!r}")
    found: dict[str, list[str]] = {"av": [], "ar": [], "base": []}
    for model in catalog.list_models():
        if model.architecture and model.architecture != pair["architecture"]:
            continue  # the catalog read this header already
        kind = classify(_header(str(model.path)), pair)
        if kind:
            found[kind].append(str(model.path))
    return {
        **pair,
        "files": {k: sorted(v) for k, v in found.items()},
        "ready": all(found.values()),
    }


def list_pairs() -> list[dict[str, Any]]:
    return [find_pair(pid) for pid in PAIRS]


def resolve(
    pair_id: str,
    overrides: dict[str, str | None],
    parts: tuple[str, ...] = ("base", "av", "ar"),
) -> dict[str, Any]:
    """The pair with one path per part: an override wins, else the first found."""
    found = find_pair(pair_id)
    chosen: dict[str, str] = {}
    for part in parts:
        path = overrides.get(part) or (found["files"][part] or [None])[0]
        if not path:
            raise NlaError(
                f"no {part.upper()} GGUF for {found['label']} — see the Library for the files"
            )
        if not Path(path).is_file():
            raise NlaError(f"no such GGUF: {path}")
        chosen[part] = path
    return {**found, "paths": chosen}


# ── vectors from a trace ─────────────────────────────────────────────────────


def trace_vectors(trace: Any, pair: dict[str, Any]) -> dict[str, Any]:
    """Every position's `l_out` at the pair's layer from a trace of the base model:
    the prompt pass, then one column per generated token."""
    from backend.modules.interp import sae
    from backend.modules.llamacpp import lens

    model_path = str(trace.manifest.get("modelPath") or "")
    if classify(_header(model_path), pair) != "base":
        raise NlaError(
            f"the trace is of {Path(model_path).name or 'an unknown model'}, not the"
            f" pair's base model ({pair['baseRepo']})"
        )
    columns: list[np.ndarray] = []
    for p in sorted({r.pass_index for r in trace.records}):
        try:
            res = lens.read_residuals(trace, p)
        except lens.LensError:
            continue
        m = res.by_layer.get(pair["layer"])
        if m is None:
            continue
        if p == 0 and m.shape[1] != res.n_tokens:
            raise NlaError("the trace recorded only part of the prompt at this layer")
        columns.extend(m.T)
    if not columns:
        raise NlaError(f"the trace has no l_out at layer {pair['layer']}")
    tokens = [str(t.get("text", "")) for t in sae.trace_tokens(trace)]
    return {"tokens": tokens[: len(columns)], "vectors": np.stack(columns)}


# ── readouts ─────────────────────────────────────────────────────────────────


def readouts_root() -> Path:
    return paths.data_dir() / "interp" / "nla"


def save_readout(
    result: dict[str, Any], gold: dict[int, np.ndarray] | None = None
) -> dict[str, Any]:
    """Store a run's readout as JSON, and the explained positions' original
    vectors beside it (`<id>.npz`) so an edited explanation can be scored later."""
    root = readouts_root()
    root.mkdir(parents=True, exist_ok=True)
    rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    stored = {**result, "id": rid, "createdAt": time.time()}
    if gold:
        with open(root / f".{rid}.npz.tmp", "wb") as fh:
            np.savez(fh, **{str(p): np.asarray(v, np.float32) for p, v in gold.items()})
        (root / f".{rid}.npz.tmp").replace(root / f"{rid}.npz")
    tmp = root / f".{rid}.json.tmp"
    tmp.write_text(json.dumps(stored), encoding="utf-8")
    tmp.replace(root / f"{rid}.json")
    return stored


def load_gold(rid: str, position: int) -> np.ndarray:
    """The original vector at an explained position of a stored readout."""
    load_readout(rid)  # validates the id and that the readout exists
    path = readouts_root() / f"{rid}.npz"
    try:
        with np.load(path) as data:
            return np.asarray(data[str(position)], np.float32)
    except (OSError, KeyError) as exc:
        raise NlaError(f"readout {rid} kept no vector for position {position}") from exc


def load_readout(rid: str) -> dict[str, Any]:
    if not re.fullmatch(r"[\w\-]+", rid):
        raise NlaError(f"bad readout id {rid!r}")
    path = readouts_root() / f"{rid}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NlaError(f"no readout {rid!r}") from exc


def list_readouts(limit: int = 30) -> list[dict[str, Any]]:
    """Newest first, without the per-sample text: what a picker shows."""
    root = readouts_root()
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.glob("*.json"), reverse=True)[:limit]:
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append(
            {
                "id": r["id"],
                "pairId": r.get("pairId"),
                "source": r.get("source"),
                "positions": len(r.get("positions") or []),
                "meanCos": r.get("summary", {}).get("meanCos"),
                "createdAt": r.get("createdAt"),
            }
        )
    return out


def summarize(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Across a readout: the best sample's cosine per position, how many carried."""
    scored = [
        p["best"]
        for p in positions
        if p.get("best") and p["best"].get("cos") is not None
    ]
    best = [b["cos"] for b in scored]
    nulls = [b["nullCos"] for b in scored if b.get("nullCos") is not None]
    verdicts = [verdict(b["cos"], b.get("nullCos")) for b in scored]
    return {
        "positions": len(positions),
        "meanCos": round(float(np.mean(best)), 4) if best else None,
        "meanNullCos": round(float(np.mean(nulls)), 4) if nulls else None,
        "carried": verdicts.count("carried"),
        "partial": verdicts.count("partial"),
        "drifted": verdicts.count("drifted"),
        "flagged": sum(1 for p in positions if p.get("flags")),
    }


async def run(
    spec: dict[str, Any], timeout: float = 60 * 60
) -> AsyncIterator[dict[str, Any]]:
    """`nla_runner` through the tracer's subprocess plumbing."""
    from backend.modules.llamacpp import trace_runner

    async for event in trace_runner.stream_module(
        "backend.modules.interp.nla_runner", spec, timeout
    ):
        yield event
