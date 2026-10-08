"""Generate the GGUF header conformance fixture for the browser engine.

    PYTHONPATH=. uv run python scripts/gen_webml_gguf_fixture.py

Writes a tiny Qwen3-shaped GGUF with llama.cpp's own writer (the `gguf` package)
and, beside it, what `backend/modules/interpretability/gguf.py` reads from it.
`packages/webml/src/gguf/__tests__/fixture.test.ts` parses the same file with
`parse.ts` and must agree field for field, which is what keeps the two readers
from drifting apart. See docs/architecture/webml-gguf-engine.mdx.

It also writes `quant-golden.json`: blocks quantized and dequantized by the same
package, the oracle `quant.test.ts` checks the CPU dequantizers against (which in
turn are the oracle for the WGSL kernels).

The tensor data is seeded random bytes in each type's block layout: the fixture
pins the *header*, not any dequantized value. Regenerate only when the fixture
itself needs to change, and make the TS suite pass before committing.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from gguf import GGMLQuantizationType as Q
from gguf import GGUFValueType, GGUFWriter
from gguf.quants import dequantize, quantize

from backend.modules.interpretability.gguf import read_header

OUT = Path("packages/webml/src/gguf/__tests__/fixtures")
GGUF = OUT / "tiny-qwen3.gguf"
EXPECTED = OUT / "tiny-qwen3.expected.json"
GOLDEN = OUT / "quant-golden.json"

# (name, ggml shape [row length, rows], type). Row lengths are whole blocks of the
# type: 32 for Q8_0 / Q4_0, 256 for the K-quants.
TENSORS: list[tuple[str, list[int], Q]] = [
    ("token_embd.weight", [64, 32], Q.Q8_0),
    ("blk.0.attn_norm.weight", [64], Q.F32),
    ("blk.0.attn_q.weight", [64, 64], Q.Q4_0),
    ("blk.0.attn_k.weight", [256, 2], Q.Q4_K),
    ("blk.0.attn_q_norm.weight", [16], Q.F32),
    ("blk.0.ffn_up.weight", [64, 8], Q.F16),
    ("blk.0.ffn_down.weight", [256, 2], Q.Q6_K),
    ("output_norm.weight", [64], Q.F32),
]

# Block (elements, bytes) for the types above, to size the raw bytes.
BLOCKS = {
    Q.F32: (1, 4),
    Q.F16: (1, 2),
    Q.Q4_0: (32, 18),
    Q.Q8_0: (32, 34),
    Q.Q4_K: (256, 144),
    Q.Q6_K: (256, 210),
}

VOCAB = ["<|endoftext|>", "<|im_start|>", "<|im_end|>", "Ġthe", "你好", "🙂", "a", "b"]


def write() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(6)
    w = GGUFWriter(str(GGUF), "qwen3")
    w.add_name("tiny-qwen3")
    w.add_block_count(1)
    w.add_context_length(4096)
    w.add_embedding_length(64)
    w.add_feed_forward_length(8)
    w.add_head_count(4)
    w.add_head_count_kv(2)
    w.add_key_length(16)
    w.add_value_length(16)
    w.add_rope_freq_base(1_000_000.0)
    w.add_layer_norm_rms_eps(1e-6)
    w.add_tokenizer_model("gpt2")
    w.add_tokenizer_pre("qwen2")
    w.add_token_list(VOCAB)
    w.add_token_types([3, 3, 3, 1, 1, 1, 1, 1])
    w.add_token_merges(["Ġ t", "Ġt he"])
    w.add_eos_token_id(2)
    w.add_add_bos_token(False)
    w.add_chat_template(
        "{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content }}<|im_end|>\n{% endfor %}"
    )
    # Types the helpers above do not reach: u64, i64, f64, a float array.
    w.add_uint64("tiny.u64", 2**40 + 3)
    w.add_int64("tiny.i64", -(2**33))
    w.add_float64("tiny.f64", 0.1)
    w.add_array("tiny.scores", [0.0, -1.5, 2.25])
    w.add_key_value("tiny.u8", 7, GGUFValueType.UINT8)

    for name, shape, qtype in TENSORS:
        block_elems, block_bytes = BLOCKS[qtype]
        rows = shape[1] if len(shape) > 1 else 1
        row_bytes = shape[0] // block_elems * block_bytes
        raw = rng.integers(0, 256, size=(rows, row_bytes), dtype=np.uint8)
        w.add_tensor(name, raw, raw_dtype=qtype)

    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()


def jsonable(value: object) -> object:
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    # u64/i64 beyond 2^53 would lose precision as a JSON number.
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53:
        return {"bigint": str(value)}
    return value


def dump() -> None:
    h = read_header(GGUF)
    EXPECTED.write_text(
        json.dumps(
            {
                "version": h.version,
                "alignment": h.alignment,
                "dataOffset": h.data_offset,
                "fileSize": h.file_size,
                "metadata": {k: jsonable(v) for k, v in h.metadata.items()},
                "tensors": [
                    {
                        "name": t.name,
                        "shape": list(t.shape),
                        "type": t.type_id,
                        "typeName": t.type_name,
                        "offset": t.offset,
                        "elements": t.elements,
                        "bytes": t.n_bytes,
                    }
                    for t in h.tensors
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def golden() -> None:
    """Per type: raw block bytes (hex) and the values gguf dequantizes them to."""
    rng = np.random.default_rng(60)
    # Mixed magnitudes, including an all-zero block (scale 0) and a large one.
    values = rng.standard_normal((3, 64)).astype(np.float32)
    values[1] *= 0
    values[2] *= 1000
    out = {}
    for qtype in (Q.F32, Q.F16, Q.Q8_0, Q.Q4_0):
        if qtype == Q.F32:
            raw = values.reshape(-1).view(np.uint8)
        elif qtype == Q.F16:
            raw = values.astype(np.float16).reshape(-1).view(np.uint8)
        else:
            raw = quantize(values, qtype).reshape(-1)
        deq = dequantize(raw, qtype).reshape(-1)
        out[qtype.name] = {
            "type": int(qtype),
            "hex": raw.tobytes().hex(),
            "values": [float(v) for v in deq.astype(np.float32)],
        }
    # K-quants: numpy cannot quantize to them, so the blocks are random bytes with
    # sane f16 scales written in (random scale bytes would include NaN and Inf).
    for qtype, size, halves in (
        (Q.Q4_K, 144, (0, 2)),
        (Q.Q5_K, 176, (0, 2)),
        (Q.Q6_K, 210, (208,)),
    ):
        raw = rng.integers(0, 256, size=3 * size, dtype=np.uint8)
        for b in range(3):
            for h in halves:
                scale = np.float16(rng.uniform(0.001, 0.05))
                raw[b * size + h : b * size + h + 2] = np.frombuffer(scale.tobytes(), np.uint8)
        deq = dequantize(raw, qtype).reshape(-1)
        out[qtype.name] = {
            "type": int(qtype),
            "hex": raw.tobytes().hex(),
            "values": [float(v) for v in deq.astype(np.float32)],
        }
    GOLDEN.write_text(json.dumps(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write()
    dump()
    golden()
    print(f"wrote {GGUF} ({GGUF.stat().st_size} bytes) and {EXPECTED}")
