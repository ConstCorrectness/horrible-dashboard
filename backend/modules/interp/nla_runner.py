"""A Natural Language Autoencoder on llama.cpp: read, explain, reconstruct, score.

Run as a subprocess — `python -m backend.modules.interp.nla_runner <spec.json>` —
through the tracer's runner, like `axis_runner`. The three models load one at a
time, each freed before the next:

1. **base** — the text is decoded once and each position's `l_out` at the pair's
   layer kept (skipped when the spec brings vectors, e.g. from a trace).
2. **AV** — the canonical prompt is decoded up to the injection token, the vector
   (rescaled to the pair's norm) goes in *as that position's embedding* through a
   `llama_batch` with `embd` set, and the rest of the prompt follows as tokens.
   The prefix stays in the KV cache; each sample rolls back to the injection site.
3. **AR** — each explanation in the AR's template is decoded; the last token's
   `l_out` at the pair's layer (the AR has no blocks above it, and no final norm
   is applied — that residual is what its head was trained on) times the value
   head is the reconstruction, scored against the original by angle.

Modes, `spec["mode"]`: **explain** (all three) and **score** (AR only, for
explanations someone wrote or edited, against vectors the spec brings).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from backend.modules.interp import nla
from backend.modules.interp.axis_runner import Session
from backend.modules.llamacpp import tracer

emit = tracer.emit


def _value_head(path: str) -> np.ndarray:
    """The AR's d×d head, stored in its GGUF as `nla.value_head.weight` (BF16),
    as a float32 [out, in] matrix: `pred = W @ h`."""
    from gguf import GGUFReader

    reader = GGUFReader(path)
    for t in reader.tensors:
        if t.name != "nla.value_head.weight":
            continue
        shape = tuple(int(x) for x in reversed(t.shape))
        if t.tensor_type.name == "BF16":
            u = np.asarray(t.data).view(np.uint16).astype(np.uint32) << 16
            return u.view(np.float32).reshape(shape).copy()
        if t.tensor_type.name == "F32":
            return np.asarray(t.data, np.float32).reshape(shape).copy()
        raise RuntimeError(f"value head is {t.tensor_type.name}, expected BF16 or F32")
    raise RuntimeError(
        f"{Path(path).name} carries no nla.value_head.weight — not an NLA AR conversion"
    )


def _load_ar(spec: dict[str, Any], layer: int, n_ctx: int) -> Session:
    """The AR GGUF in llama.cpp. Its value head is an extra tensor llama.cpp's
    loader does not know; a copy without it (`.llama.gguf` beside the file, or in
    `stripDir`) is made once if the loader refuses the original."""
    try:
        return Session({**spec, "modelPath": spec["arPath"]}, {layer}, n_ctx)
    except RuntimeError as exc:
        if "could not load" not in str(exc):
            raise
    stripped = _stripped_copy(str(spec["arPath"]))
    return Session({**spec, "modelPath": stripped}, {layer}, n_ctx)


def _stripped_copy(path: str) -> str:
    from gguf import GGUFReader, GGUFWriter

    src = Path(path)
    dst = src.with_name(src.stem + ".llama.gguf")
    if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
        return str(dst)
    emit({"status": "strip", "file": dst.name})
    reader = GGUFReader(str(src))
    arch = str(reader.fields["general.architecture"].parts[-1].tobytes(), "utf-8")
    writer = GGUFWriter(str(dst) + ".tmp", arch)
    for field in reader.fields.values():
        if field.name.startswith("GGUF.") or field.name == "general.architecture":
            continue
        value = field.contents()
        if field.name == "nla.role":
            # Not an AR on its own (no head): `nla.classify` must not offer it.
            value = "ar-backbone"
        writer.add_key_value(
            field.name,
            value,
            field.types[0],
            field.types[-1] if len(field.types) > 1 else None,
        )
    for t in reader.tensors:
        if t.name == "nla.value_head.weight":
            continue
        data = np.asarray(t.data)
        # The reader's array shape is the writer's input: element shape for
        # float tensors, byte shape for quantized ones (it converts back).
        writer.add_tensor_info(
            t.name, list(data.shape), data.dtype, t.n_bytes, t.tensor_type
        )
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_ti_data_to_file()
    for t in reader.tensors:
        if t.name != "nla.value_head.weight":
            writer.write_tensor_data(np.asarray(t.data))
    writer.close()
    Path(str(dst) + ".tmp").replace(dst)
    return str(dst)


# ── base: the activations ────────────────────────────────────────────────────


def read_base(
    spec: dict[str, Any], pair: dict[str, Any]
) -> tuple[list[str], np.ndarray]:
    layer = pair["layer"]
    cap = int(spec.get("tokenCap") or 2048)
    s = Session({**spec, "modelPath": spec["basePath"]}, {layer}, cap + 16)
    try:
        if spec.get("messages"):
            text = s.template(list(spec["messages"]), open_turn=False)
        else:
            text = str(spec.get("text") or "")
        if not text:
            raise RuntimeError("nothing to read: no text and no messages")
        tokens = s.tokenize(text)
        if len(tokens) > cap:
            tokens = tokens[-cap:]
        emit({"status": "decode", "model": "base", "tokens": len(tokens)})
        s.reset()
        captured = s.decode(tokens)
        if layer not in captured:
            raise RuntimeError(f"no l_out-{layer} came back from the base model")
        return [s.piece(t) for t in tokens], captured[layer]
    finally:
        s.close()


# ── AV: the explanation ──────────────────────────────────────────────────────


def _decode_embedding(s: Session, vector: np.ndarray, position: int) -> None:
    """One position fed as an embedding instead of a token."""
    ll = s.ll
    batch = ll.llama_batch_init(1, s.n_embd, 1)
    try:
        embd = np.ctypeslib.as_array(batch.embd, shape=(s.n_embd,))
        embd[:] = vector.astype(np.float32)
        batch.pos[0] = position
        batch.n_seq_id[0] = 1
        batch.seq_id[0][0] = 0
        batch.logits[0] = 0
        batch.n_tokens = 1
        if ll.llama_decode(s.ctx, batch) != 0:
            raise RuntimeError("llama_decode refused the embedding batch")
    finally:
        ll.llama_batch_free(batch)


def _sample(s: Session, temperature: float, rng: np.random.Generator) -> int:
    logits = s.ll.llama_get_logits_ith(s.ctx, -1)
    z = np.ctypeslib.as_array(logits, shape=(s.n_vocab,)).astype(np.float64)
    if temperature <= 0:
        return int(z.argmax())
    z = (z - z.max()) / temperature
    p = np.exp(z)
    p /= p.sum()
    return int(rng.choice(len(p), p=p))


class Verbalizer:
    def __init__(self, spec: dict[str, Any], pair: dict[str, Any]) -> None:
        self.pair = pair
        self.s = Session({**spec, "modelPath": spec["avPath"]}, set(), 1024)
        if self.s.n_embd != pair["nEmbd"]:
            raise RuntimeError(
                f"the AV is d={self.s.n_embd}, the pair is d={pair['nEmbd']}"
            )
        tokens = self.s.tokenize(nla.av_prompt(pair))
        self.site = nla.injection_site(
            tokens, pair
        )  # NlaError → refused, never guessed
        self.prefix, self.suffix = tokens[: self.site], tokens[self.site + 1 :]
        self.s.reset()
        self.s.decode(self.prefix)

    def explain(
        self,
        vector: np.ndarray,
        max_tokens: int,
        temperature: float,
        rng: np.random.Generator,
    ) -> dict[str, Any]:
        s, pair = self.s, self.pair
        norm = float(np.linalg.norm(vector))
        scaled = vector / max(norm, 1e-12) * pair["injectionScale"]
        s.rollback(self.site)
        _decode_embedding(s, scaled, self.site)
        s.decode(self.suffix)
        out: list[int] = []
        for _ in range(max_tokens):
            token = _sample(s, temperature, rng)
            if s.is_eog(token):
                break
            out.append(token)
            if len(out) % 8 == 0 and "</explanation>" in s.text(out[-12:]):
                break
            s.decode([token])
        raw = s.text(out)
        explanation, closed = nla.extract_explanation(raw)
        return {
            "raw": raw,
            "explanation": explanation,
            "closed": closed,
            "tokens": len(out),
        }

    def close(self) -> None:
        self.s.close()


# ── AR: the reconstruction ───────────────────────────────────────────────────


class Reconstructor:
    def __init__(self, spec: dict[str, Any], pair: dict[str, Any]) -> None:
        self.pair = pair
        self.head = _value_head(str(spec["arPath"]))
        self.s = _load_ar(spec, pair["layer"], 2048)
        if self.head.shape != (pair["nEmbd"], pair["nEmbd"]):
            raise RuntimeError(f"value head is {self.head.shape}, expected d×d")

    def reconstruct(self, explanation: str) -> np.ndarray:
        s = self.s
        tokens = s.tokenize(nla.ar_prompt(self.pair, explanation))
        s.reset()
        captured = s.decode(tokens)
        h = captured[self.pair["layer"]][-1]
        return self.head @ h

    def close(self) -> None:
        self.s.close()


def _score_all(
    spec: dict[str, Any],
    pair: dict[str, Any],
    items: list[tuple[np.ndarray, dict[str, Any]]],
) -> None:
    """Fill `cos`/`mse`/`nullCos` on each sample dict in place, one AR load for
    all. `nullCos` is the neutral sentence's reconstruction against the same
    vector — the floor that cos is read above (`nla.NULL_EXPLANATION`)."""
    emit({"status": "loading", "model": "ar"})
    r = Reconstructor(spec, pair)
    try:
        null = r.reconstruct(nla.NULL_EXPLANATION)
        for i, (gold, sample) in enumerate(items):
            null_cos = nla.score(null, gold, pair["mseScale"])["cos"]
            if not sample["explanation"]:
                sample.update(cos=None, mse=None, nullCos=null_cos)
                continue
            pred = r.reconstruct(sample["explanation"])
            sample.update(nla.score(pred, gold, pair["mseScale"]), nullCos=null_cos)
            emit({"status": "score", "done": i + 1, "total": len(items)})
    finally:
        r.close()


def explain(spec: dict[str, Any]) -> dict[str, Any]:
    pair = spec["pair"]
    started = time.time()
    timing: dict[str, float] = {}
    if spec.get("vectors") is not None:
        vectors = np.asarray(spec["vectors"], np.float32)
        tokens = list(spec.get("tokens") or [""] * len(vectors))
    else:
        emit({"status": "loading", "model": "base"})
        tokens, vectors = read_base(spec, pair)
        timing["base"] = round(time.time() - started, 1)
    if vectors.ndim != 2 or vectors.shape[1] != pair["nEmbd"]:
        raise RuntimeError(f"vectors must be [n, {pair['nEmbd']}]")
    positions = nla.pick_positions(
        len(vectors), spec.get("positions"), int(spec.get("limit") or 3)
    )
    samples_per = max(1, int(spec.get("samples") or 1))
    max_tokens = int(spec.get("maxTokens") or nla.DEFAULT_MAX_TOKENS)
    temperature = float(
        spec["temperature"]
        if spec.get("temperature") is not None
        else nla.DEFAULT_TEMPERATURE
    )
    rng = np.random.default_rng(spec.get("seed"))

    t0 = time.time()
    emit({"status": "loading", "model": "av"})
    v = Verbalizer(spec, pair)
    rows: list[dict[str, Any]] = []
    generated = 0
    try:
        for n, p in enumerate(positions):
            gold = vectors[p]
            norm = float(np.linalg.norm(gold))
            row = {
                "position": p,
                "token": tokens[p] if p < len(tokens) else "",
                "norm": round(norm, 2),
                "flags": nla.position_flags(p, norm, pair),
                "samples": [],
            }
            for k in range(samples_per):
                sample = v.explain(gold, max_tokens, temperature, rng)
                generated += sample["tokens"]
                row["samples"].append(sample)
                emit(
                    {
                        "status": "explain",
                        "done": n * samples_per + k + 1,
                        "total": len(positions) * samples_per,
                        "position": p,
                        "explanation": sample["explanation"][:200],
                    }
                )
            rows.append(row)
    finally:
        v.close()
    timing["av"] = round(time.time() - t0, 1)

    t0 = time.time()
    _score_all(
        spec,
        pair,
        [(vectors[row["position"]], s) for row in rows for s in row["samples"]],
    )
    timing["ar"] = round(time.time() - t0, 1)
    for row in rows:
        scored = [s for s in row["samples"] if s.get("cos") is not None]
        row["best"] = max(scored, key=lambda s: s["cos"]) if scored else None
        best = row["best"]
        row["verdict"] = nla.verdict(
            best["cos"] if best else None, best.get("nullCos") if best else None
        )
    return {
        "tokens": tokens,
        "norms": [round(float(x), 2) for x in np.linalg.norm(vectors, axis=1)],
        "positions": rows,
        "gold": {
            str(row["position"]): vectors[row["position"]].tolist() for row in rows
        },
        "timing": {**timing, "total": round(time.time() - started, 1)},
        "avTokensPerSecond": round(generated / timing["av"], 2)
        if timing["av"]
        else None,
        "settings": {
            "samples": samples_per,
            "maxTokens": max_tokens,
            "temperature": temperature,
            "seed": spec.get("seed"),
        },
    }


def score(spec: dict[str, Any]) -> dict[str, Any]:
    """AR only: `items` = [{explanation, vector}] → each with cos and mse."""
    pair = spec["pair"]
    items = [
        (
            np.asarray(item["vector"], np.float32),
            {"explanation": str(item["explanation"])},
        )
        for item in spec["items"]
    ]
    _score_all(spec, pair, items)
    return {"scores": [s for _, s in items]}


MODES = {"explain": explain, "score": score}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        emit({"error": "usage: python -m backend.modules.interp.nla_runner <spec>"})
        return 2
    try:
        spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        result = MODES[str(spec.get("mode"))](spec)
    except ImportError as exc:
        emit({"error": f"llama-cpp-python is not installed here ({exc})"})
        return 1
    except Exception as exc:  # noqa: BLE001 — the runner's only report is this line
        emit({"error": f"{type(exc).__name__}: {exc}"})
        return 1
    emit({"status": "done", "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
