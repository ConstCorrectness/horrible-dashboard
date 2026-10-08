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
import json
import os
from pathlib import Path

import numpy as np
from gguf import GGMLQuantizationType as Q
from gguf import GGUFWriter
from gguf.quants import quantize
from llama_cpp import Llama

OUT = Path("packages/webml/kernel-tests/fixtures")

EMBD, FFN, LAYERS, HEADS, KV_HEADS, VOCAB, CTX = 64, 96, 2, 4, 2, 64, 64
HEAD_DIM = EMBD // HEADS
PROMPT_LEN, GENERATE = 8, 32

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


def weights(rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Float weights in numpy order ([out, in]), scaled so activations stay O(1)."""

    def linear(out: int, inp: int) -> np.ndarray:
        return (rng.standard_normal((out, inp)) / np.sqrt(inp)).astype(np.float32)

    def norm(n: int) -> np.ndarray:
        return (1 + 0.1 * rng.standard_normal(n)).astype(np.float32)

    w = {
        "token_embd": rng.standard_normal((VOCAB, EMBD)).astype(np.float32),
        "output_norm": norm(EMBD),
        # Peaky enough that greedy picks are not near-ties.
        "output": 3 * linear(VOCAB, EMBD),
    }
    for layer in range(LAYERS):
        p = f"blk.{layer}."
        w[p + "attn_norm"] = norm(EMBD)
        w[p + "attn_q"] = linear(HEADS * HEAD_DIM, EMBD)
        w[p + "attn_k"] = linear(KV_HEADS * HEAD_DIM, EMBD)
        w[p + "attn_v"] = linear(KV_HEADS * HEAD_DIM, EMBD)
        w[p + "attn_output"] = linear(EMBD, HEADS * HEAD_DIM)
        w[p + "ffn_norm"] = norm(EMBD)
        w[p + "ffn_gate"] = linear(FFN, EMBD)
        w[p + "ffn_up"] = linear(FFN, EMBD)
        w[p + "ffn_down"] = linear(EMBD, FFN)
    return w


def write_model(path: Path, w: dict[str, np.ndarray], mixed: bool) -> None:
    g = GGUFWriter(str(path), "llama")
    g.add_name(path.stem)
    g.add_block_count(LAYERS)
    g.add_context_length(CTX)
    g.add_embedding_length(EMBD)
    g.add_feed_forward_length(FFN)
    g.add_head_count(HEADS)
    g.add_head_count_kv(KV_HEADS)
    g.add_rope_dimension_count(HEAD_DIM)
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
        if mixed and name == "output":
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
        ref = reference(model, prompt, forced if mixed else None)
        (OUT / f"{stem}.expected.json").write_text(
            json.dumps(ref) + "\n", encoding="utf-8"
        )
        print(f"{model}: {model.stat().st_size} bytes, generated {ref['generated']}")


if __name__ == "__main__":
    main()
