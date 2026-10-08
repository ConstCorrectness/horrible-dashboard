"""Forward-pass parity references for the browser GGUF engine, from llama.cpp.

    uv run --with llama-cpp-python python scripts/gen_webml_llama_parity.py
    uv run --with llama-cpp-python python scripts/gen_webml_llama_parity.py \
        --model SmolLM2-360M-Instruct-Q8_0.gguf --out smollm2.expected.json

(`llama-cpp-python` builds llama.cpp from source on first install; it is only
needed to regenerate, never to run the tests.)

Writes two tiny random-weight `llama` models with llama.cpp's own writer and, for
each, what llama.cpp computes on them: a prompt, the greedy continuation, and the
logits at every position. `packages/webml/kernel-tests/forward.test.ts` runs the
same files through the WGSL engine and must agree. This is 6.1's exit check made
runnable without the Hub.

With `--model`, it instead records llama.cpp's greedy run on a real GGUF: the
prompt's token ids (llama.cpp's own tokenizer), then each generated token with the
top-5 probabilities it was picked from. `kernel-tests/real-parity.test.ts` replays
that in the browser (see its header for how to point it at the files).

- `tiny-llama-f32`: every weight F32, a separate output head. llama.cpp's f32
  matmuls do not quantize activations, so agreement here is tight.
- `tiny-llama-mixed`: Q8_0, Q4_0 and F16 weights, tied embeddings. llama.cpp
  quantizes the *activations* to q8_0 (or f16) before those matmuls; the engine
  keeps them f32. Agreement is looser, so this one is fed a fixed random sequence
  (teacher forcing) and compared on logits, not on greedy picks.

Both run llama.cpp with an f32 KV cache and no flash attention, the engine's
arithmetic. Regenerate only when the fixture itself must change.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from gguf import GGMLQuantizationType as Q
from gguf import GGUFWriter
from gguf.quants import quantize
import llama_cpp
from llama_cpp import Llama

OUT = Path("packages/webml/kernel-tests/fixtures")

VOCAB, CTX = 64, 64
PROMPT_LEN, GENERATE = 8, 32


@dataclass(frozen=True)
class Shape:
    arch: str = "llama"
    embd: int = 64
    ffn: int = 96
    layers: int = 2
    heads: int = 4
    kv_heads: int = 2
    head_dim: int = 16
    #: Qwen3's per-head RMS norms on Q and K.
    qk_norm: bool = False
    #: Llama 3's rope_freqs tensor (per-pair frequency divisors).
    rope_freqs: bool = False


TINY = Shape()
# Q/K norms, NEOX rope, and a head size that is not embd / heads (128 ≠ 64).
QWEN3 = Shape(arch="qwen3", head_dim=32, qk_norm=True)
# Every row a whole number of 256-value K-quant super-blocks.
LLAMA3_K = Shape(embd=256, ffn=512, layers=1, heads=4, kv_heads=2, head_dim=64, rope_freqs=True)
QWEN3_K = Shape(
    arch="qwen3",
    embd=256,
    ffn=512,
    layers=1,
    heads=2,
    kv_heads=1,
    head_dim=128,
    qk_norm=True,
)

# Per-tensor types for the mixed model. Norms stay F32, as llama.cpp's converter
# leaves every 1-D tensor.
MIXED = {
    "token_embd": Q.Q8_0,
    "attn_q": Q.Q4_0,
    "attn_k": Q.Q8_0,
    "attn_v": Q.F16,
    "attn_output": Q.Q4_0,
    "ffn_gate": Q.Q8_0,
    "ffn_up": Q.Q4_0,
    "ffn_down": Q.F16,
}

# llama.cpp's LLAMA_FTYPE values for llama_model_quantize.
FTYPE_Q4_K_M, FTYPE_Q5_K_M = 15, 17


def weights(rng: np.random.Generator, shape: Shape = TINY) -> dict[str, np.ndarray]:
    """Float weights in numpy order ([out, in]), scaled so activations stay O(1)."""
    e, q, kv = shape.embd, shape.heads * shape.head_dim, shape.kv_heads * shape.head_dim

    def linear(out: int, inp: int) -> np.ndarray:
        return (rng.standard_normal((out, inp)) / np.sqrt(inp)).astype(np.float32)

    def norm(n: int) -> np.ndarray:
        return (1 + 0.1 * rng.standard_normal(n)).astype(np.float32)

    w = {
        "token_embd": rng.standard_normal((VOCAB, e)).astype(np.float32),
        "output_norm": norm(e),
        # Peaky enough that greedy picks are not near-ties.
        "output": 3 * linear(VOCAB, e),
    }
    for layer in range(shape.layers):
        p = f"blk.{layer}."
        w[p + "attn_norm"] = norm(e)
        w[p + "attn_q"] = linear(q, e)
        w[p + "attn_k"] = linear(kv, e)
        w[p + "attn_v"] = linear(kv, e)
        w[p + "attn_output"] = linear(e, q)
        w[p + "ffn_norm"] = norm(e)
        w[p + "ffn_gate"] = linear(shape.ffn, e)
        w[p + "ffn_up"] = linear(shape.ffn, e)
        w[p + "ffn_down"] = linear(e, shape.ffn)
        if shape.qk_norm:
            w[p + "attn_q_norm"] = norm(shape.head_dim)
            w[p + "attn_k_norm"] = norm(shape.head_dim)
    if shape.rope_freqs:
        # Llama 3 divides the low frequencies by up to 8.
        w["rope_freqs"] = np.linspace(1, 8, shape.head_dim // 2).astype(np.float32)
    return w


def write_model(
    path: Path,
    w: dict[str, np.ndarray],
    mixed: bool = False,
    shape: Shape = TINY,
    tied: bool | None = None,
) -> None:
    tied = mixed if tied is None else tied
    g = GGUFWriter(str(path), shape.arch)
    g.add_name(path.stem)
    g.add_block_count(shape.layers)
    g.add_context_length(CTX)
    g.add_embedding_length(shape.embd)
    g.add_feed_forward_length(shape.ffn)
    g.add_head_count(shape.heads)
    g.add_head_count_kv(shape.kv_heads)
    if shape.head_dim != shape.embd // shape.heads:
        g.add_key_length(shape.head_dim)
        g.add_value_length(shape.head_dim)
    g.add_rope_dimension_count(shape.head_dim)
    g.add_rope_freq_base(10000.0)
    g.add_layer_norm_rms_eps(1e-5)
    # A vocabulary llama.cpp will load. The parity tests feed ids; the session tests
    # write prompts as text — `t2…t63` are user-defined tokens, matched literally,
    # so "t5t9" is exactly [5, 9] — through a template that concatenates contents.
    g.add_tokenizer_model("gpt2")
    g.add_tokenizer_pre("default")
    g.add_token_list(["<s>", "</s>"] + [f"t{i}" for i in range(2, VOCAB)])
    g.add_token_types([3, 3] + [4] * (VOCAB - 2))
    g.add_token_merges(["t 1"])
    g.add_bos_token_id(0)
    g.add_eos_token_id(1)
    g.add_add_bos_token(False)
    g.add_chat_template("{% for m in messages %}{{ m.content }}{% endfor %}")

    for name, value in w.items():
        if tied and name == "output":
            continue  # tied: the head is token_embd
        kind = name.split(".")[-1]
        qtype = MIXED.get(kind, Q.F32) if mixed else Q.F32
        if value.ndim == 1 or qtype == Q.F32:
            g.add_tensor(f"{name}.weight", value)
        elif qtype == Q.F16:
            g.add_tensor(f"{name}.weight", value.astype(np.float16))
        else:
            g.add_tensor(f"{name}.weight", quantize(value, qtype), raw_dtype=qtype)

    g.write_header_to_file()
    g.write_kv_data_to_file()
    g.write_tensors_to_file()
    g.close()


def llama_quantize(src: Path, dst: Path, ftype: int) -> None:
    """Quantize with llama.cpp itself — how real Q4_K_M / Q5_K_M files are made,
    including which tensors it keeps at higher precision."""
    params = llama_cpp.llama_model_quantize_default_params()
    params.ftype = ftype
    params.nthread = 1
    rc = llama_cpp.llama_model_quantize(
        str(src).encode(), str(dst).encode(), ctypes.byref(params)
    )
    if rc != 0:
        raise RuntimeError(f"llama_model_quantize failed ({rc})")


def reference(
    path: Path, prompt: list[int], forced: list[int] | None
) -> dict[str, object]:
    """llama.cpp's logits after every token. With `forced`, those tokens follow the
    prompt; without, llama.cpp's own greedy picks do."""
    llm = Llama(
        model_path=str(path),
        n_ctx=CTX,
        n_batch=CTX,
        n_threads=1,
        logits_all=True,
        type_k=0,  # GGML_TYPE_F32
        type_v=0,
        flash_attn=False,
        verbose=False,
    )
    llm.eval(prompt)
    generated: list[int] = []
    for i in range(GENERATE):
        token = forced[i] if forced else int(np.argmax(llm.scores[llm.n_tokens - 1]))
        generated.append(token)
        llm.eval([token])
    # Logits after every token, including the last generated one.
    logits = np.asarray(llm.scores[: llm.n_tokens], dtype=np.float32)
    return {
        "model": path.name,
        "prompt": prompt,
        "forced": bool(forced),
        "generated": generated,
        "logits": [[round(float(v), 6) for v in row] for row in logits],
    }


DEFAULT_PROMPT = "<|im_start|>user\nWrite a short poem about the sea.<|im_end|>\n<|im_start|>assistant\n"


def real_model(
    model: Path, out: Path, prompt_text: str, prompt_ids: list[int] | None, n: int
) -> None:
    """llama.cpp's greedy run on a real model, with the top-5 at every step."""
    llm = Llama(
        model_path=str(model),
        n_ctx=4096,
        n_batch=512,
        n_threads=os.cpu_count() or 4,
        logits_all=True,
        type_k=0,
        type_v=0,
        flash_attn=False,
        verbose=False,
    )
    prompt = prompt_ids or llm.tokenize(
        prompt_text.encode("utf-8"), add_bos=True, special=True
    )
    llm.eval(prompt)
    steps = []
    for _ in range(n):
        row = np.asarray(llm.scores[llm.n_tokens - 1], dtype=np.float64)
        probs = np.exp(row - row.max())
        probs /= probs.sum()
        top = np.argsort(-probs)[:5]
        token = int(top[0])
        steps.append({"token": token, "top": [[int(i), float(probs[i])] for i in top]})
        llm.eval([token])
    out.write_text(
        json.dumps(
            {"model": model.name, "prompt": [int(t) for t in prompt], "steps": steps}
        )
        + "\n",
        encoding="utf-8",
    )
    text = llm.detokenize([s["token"] for s in steps]).decode("utf-8", errors="replace")
    print(f"{out}: {len(prompt)} prompt tokens, generated: {text!r}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--model", type=Path, help="a real GGUF to record a reference run of"
    )
    ap.add_argument(
        "--out", type=Path, help="where to write the reference (with --model)"
    )
    ap.add_argument(
        "--prompt", default=DEFAULT_PROMPT, help="prompt text (special tokens allowed)"
    )
    ap.add_argument(
        "--prompt-ids", help="comma-separated token ids instead of --prompt"
    )
    ap.add_argument("--tokens", type=int, default=64, help="greedy tokens to record")
    args = ap.parse_args()
    if args.model:
        if not args.out:
            ap.error("--model needs --out")
        ids = [int(t) for t in args.prompt_ids.split(",")] if args.prompt_ids else None
        real_model(args.model, args.out, args.prompt, ids, args.tokens)
        return

    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(61)
    w = weights(rng)
    prompt = [int(t) for t in rng.integers(2, VOCAB, size=PROMPT_LEN)]
    forced = [int(t) for t in rng.integers(2, VOCAB, size=GENERATE)]
    for stem, mixed in (("tiny-llama-f32", False), ("tiny-llama-mixed", True)):
        model = OUT / f"{stem}.gguf"
        write_model(model, w, mixed)
        record(model, reference(model, prompt, forced if mixed else None))

    # 6.3: Qwen3 in F32 (greedy, tight), and K-quant models made by llama.cpp's own
    # quantizer from F32 sources (teacher-forced: llama.cpp rounds activations to
    # q8_K for K-quant dot products; the engine keeps them f32).
    qwen3 = OUT / "tiny-qwen3-f32.gguf"
    write_model(qwen3, weights(np.random.default_rng(62), QWEN3), shape=QWEN3)
    record(qwen3, reference(qwen3, prompt, None))
    for stem, shape, seed, ftype in (
        ("tiny-llama3-q4km", LLAMA3_K, 63, FTYPE_Q4_K_M),
        ("tiny-qwen3-q5km", QWEN3_K, 64, FTYPE_Q5_K_M),
    ):
        model = OUT / f"{stem}.gguf"
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "f32.gguf"
            write_model(
                src, weights(np.random.default_rng(seed), shape), shape=shape, tied=True
            )
            llama_quantize(src, model, ftype)
        record(model, reference(model, prompt, forced))


def record(model: Path, ref: dict[str, object]) -> None:
    out = model.with_name(model.name.removesuffix(".gguf") + ".expected.json")
    out.write_text(json.dumps(ref) + "\n", encoding="utf-8")
    print(f"{model}: {model.stat().st_size} bytes, generated {ref['generated']}")


if __name__ == "__main__":
    main()
