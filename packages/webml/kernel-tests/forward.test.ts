/**
 * The 6.1, 6.3 and 6.6 checks, runnable without the Hub: whole forward passes
 * through the WGSL engine against llama.cpp on the same GGUF files
 * (scripts/gen_webml_llama_parity.py). The K-quant files were made by llama.cpp's
 * own quantizer, as real Q4_K_M / Q5_K_M files are.
 */
import { describe, expect, it } from 'vitest';

import { readGgufHeader } from '../src/gguf/parse';
import { GgufRuntime, type LoadOptions } from '../src/gguf/runtime';
import { bytesSource } from '../src/gguf/source';
import { gpu } from './harness';
import gemma3Expected from './fixtures/tiny-gemma3-f32.expected.json';
import gemma3Url from './fixtures/tiny-gemma3-f32.gguf?url';
import f32Expected from './fixtures/tiny-llama-f32.expected.json';
import f32Url from './fixtures/tiny-llama-f32.gguf?url';
import mixedExpected from './fixtures/tiny-llama-mixed.expected.json';
import mixedUrl from './fixtures/tiny-llama-mixed.gguf?url';
import llama3Expected from './fixtures/tiny-llama3-q4km.expected.json';
import llama3Url from './fixtures/tiny-llama3-q4km.gguf?url';
import qwen2Expected from './fixtures/tiny-qwen2-f32.expected.json';
import qwen2Url from './fixtures/tiny-qwen2-f32.gguf?url';
import qwen3Expected from './fixtures/tiny-qwen3-f32.expected.json';
import qwen3Url from './fixtures/tiny-qwen3-f32.gguf?url';
import qwen3kExpected from './fixtures/tiny-qwen3-q5km.expected.json';
import qwen3kUrl from './fixtures/tiny-qwen3-q5km.gguf?url';

interface Expected {
  prompt: number[];
  generated: number[];
  forced: boolean;
  /** llama.cpp's logits after each token of prompt + generated. */
  logits: number[][];
}

/**
 * The fixture's runtime. An f32 KV cache unless asked otherwise: the references
 * were recorded with llama.cpp's cache in f32, so that is the exact comparison.
 */
async function load(url: string, options: LoadOptions = {}): Promise<GgufRuntime> {
  const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer());
  const source = bytesSource(bytes);
  return GgufRuntime.load(await gpu(), await readGgufHeader(source), source, {
    kvCache: 'f32',
    ...options,
  });
}

/**
 * See the Gemma 3 case. Measured 2.0e-4; emulating the f16 table in the kernel
 * brought it to 3.7e-5: the gap is that table, not the engine.
 */
const GEMMA_TOLERANCE = 5e-4;

function argmax(row: ArrayLike<number>): number {
  let best = 0;
  for (let i = 1; i < row.length; i++) if (row[i] > row[best]) best = i;
  return best;
}

/** Largest |a − b| over the row, relative to the row's largest |b|. */
function rowError(got: ArrayLike<number>, want: number[]): number {
  let diff = 0;
  let mag = 0;
  for (let i = 0; i < want.length; i++) {
    diff = Math.max(diff, Math.abs(got[i] - want[i]));
    mag = Math.max(mag, Math.abs(want[i]));
  }
  return diff / mag;
}

/** Logits after every token of `tokens`, fed one step at a time. */
async function logitsAlong(rt: GgufRuntime, tokens: number[]): Promise<Float32Array[]> {
  const rows: Float32Array[] = [];
  for (let i = 0; i < tokens.length; i++) rows.push(await rt.forward([tokens[i]], i));
  return rows;
}

describe('forward pass vs llama.cpp', () => {
  it('all-F32 model: same greedy continuation, logits within 1e-5', async () => {
    const want = f32Expected as Expected;
    const rt = await load(f32Url);
    try {
      // Greedy from the prompt, choosing our own tokens.
      let logits = await rt.forward(want.prompt);
      const generated: number[] = [];
      for (let i = 0; i < want.generated.length; i++) {
        const token = argmax(logits);
        generated.push(token);
        logits = await rt.forward([token], want.prompt.length + i);
      }
      expect(generated).toEqual(want.generated);

      const rows = await logitsAlong(rt, [...want.prompt, ...want.generated]);
      const worst = Math.max(...rows.map((r, i) => rowError(r, want.logits[i])));
      expect(worst).toBeLessThan(1e-5);
    } finally {
      rt.destroy();
    }
  });

  it('the same model with every tensor split into tiny chunks gives the same logits', async () => {
    const want = f32Expected as Expected;
    // 1 KB bindings: the 64×64 embedding and head become 16 chunks of 4 rows each,
    // and the FFN matrices several chunks — every chunked path in the runtime.
    const rt = await load(f32Url, { maxBindingBytes: 1024 });
    try {
      const rows = await logitsAlong(rt, [...want.prompt, ...want.generated]);
      const worst = Math.max(...rows.map((r, i) => rowError(r, want.logits[i])));
      expect(worst).toBeLessThan(1e-5);
    } finally {
      rt.destroy();
    }
  });

  it('Q8_0 / Q4_0 / F16 model with tied embeddings: logits close under teacher forcing', async () => {
    const want = mixedExpected as Expected;
    const rt = await load(mixedUrl);
    try {
      const rows = await logitsAlong(rt, [...want.prompt, ...want.generated]);
      const errors = rows.map((r, i) => rowError(r, want.logits[i]));
      // llama.cpp quantizes activations to q8_0 / f16 before these matmuls and we
      // do not, so the rows differ by that rounding, not by a mistake.
      expect(Math.max(...errors)).toBeLessThan(2e-2);
      const agree = rows.filter((r, i) => argmax(r) === argmax(want.logits[i])).length;
      expect(agree / rows.length).toBeGreaterThanOrEqual(0.95);
    } finally {
      rt.destroy();
    }
  });

  it('Qwen3 (Q/K norms, NEOX rope, head size ≠ embd/heads), F32: same greedy continuation', async () => {
    const want = qwen3Expected as Expected;
    const rt = await load(qwen3Url);
    try {
      expect(rt.config).toMatchObject({ arch: 'qwen3', rope: 'neox', qkNorm: true, headDim: 32 });
      let logits = await rt.forward(want.prompt);
      const generated: number[] = [];
      for (let i = 0; i < want.generated.length; i++) {
        generated.push(argmax(logits));
        logits = await rt.forward([generated[i]], want.prompt.length + i);
      }
      expect(generated).toEqual(want.generated);
      const rows = await logitsAlong(rt, [...want.prompt, ...want.generated]);
      expect(Math.max(...rows.map((r, i) => rowError(r, want.logits[i])))).toBeLessThan(1e-5);
    } finally {
      rt.destroy();
    }
  });

  // 6.6. Gemma's tolerance is looser because llama.cpp's CPU GELU is a lookup
  // table of f16 inputs to f16 outputs, where the engine computes it in f32.
  for (const [name, url, expected, config, tolerance] of [
    [
      'Qwen2 (Q/K/V biases, NEOX rope)',
      qwen2Url,
      qwen2Expected,
      { arch: 'qwen2', rope: 'neox', qkvBias: true },
      1e-5,
    ],
    [
      'Gemma 3 (scaled embedding, post-norms, GELU, five windowed layers of six)',
      gemma3Url,
      gemma3Expected,
      {
        arch: 'gemma3',
        qkNorm: true,
        postNorms: true,
        ffnAct: 'gelu',
        embedScale: 8,
        slidingWindow: 5,
        swaPattern: 6,
        ropeBase: 1e6,
        ropeBaseSwa: 10000,
      },
      GEMMA_TOLERANCE,
    ],
  ] as const) {
    it(`${name}, F32: same greedy continuation`, async () => {
      const want = expected as Expected;
      const rt = await load(url);
      try {
        expect(rt.config).toMatchObject(config);
        let logits = await rt.forward(want.prompt);
        const generated: number[] = [];
        for (let i = 0; i < want.generated.length; i++) {
          generated.push(argmax(logits));
          logits = await rt.forward([generated[i]], want.prompt.length + i);
        }
        expect(generated).toEqual(want.generated);
        const rows = await logitsAlong(rt, [...want.prompt, ...want.generated]);
        expect(Math.max(...rows.map((r, i) => rowError(r, want.logits[i])))).toBeLessThan(
          tolerance,
        );
      } finally {
        rt.destroy();
      }
    });
  }

  for (const [name, url, expected, config] of [
    [
      'Llama 3 Q4_K_M (Q4_K + Q6_K, rope_freqs, tied)',
      llama3Url,
      llama3Expected,
      { arch: 'llama', ropeFreqs: true, tiedEmbeddings: true },
    ],
    [
      'Qwen3 Q5_K_M (Q5_K + Q6_K, tied)',
      qwen3kUrl,
      qwen3kExpected,
      { arch: 'qwen3', qkNorm: true, tiedEmbeddings: true },
    ],
  ] as const) {
    it(`${name}: logits close to llama.cpp under teacher forcing`, async () => {
      const want = expected as Expected;
      const rt = await load(url);
      try {
        expect(rt.config).toMatchObject(config);
        const rows = await logitsAlong(rt, [...want.prompt, ...want.generated]);
        const errors = rows.map((r, i) => rowError(r, want.logits[i]));
        // llama.cpp rounds activations to q8_K before K-quant dot products; we do not.
        // Measured: worst 0.39%, mean 0.25%, for both files.
        expect(Math.max(...errors)).toBeLessThan(1e-2);
        const agree = rows.filter((r, i) => argmax(r) === argmax(want.logits[i])).length;
        expect(agree / rows.length).toBeGreaterThanOrEqual(0.95);
      } finally {
        rt.destroy();
      }
    });
  }

  it('refuses positions outside the context and tokens outside the vocabulary', async () => {
    const rt = await load(f32Url, { contextLength: 16, batch: 8 });
    try {
      expect(rt.context).toBe(16);
      expect(() => rt.queueStep(3, 16)).toThrow(/outside the 16-token context/);
      expect(() => rt.queueStep(64, 0)).toThrow(/outside the 64-token vocabulary/);
      expect(() => rt.queueBatch([1, 2, 3], 14, true)).toThrow(/position 16 is outside/);
      expect(() => rt.queueBatch([1, 64], 0, true)).toThrow(/outside the 64-token vocabulary/);
      expect(() => rt.queueBatch(new Array(9).fill(1), 0, true)).toThrow(/one 8-token pass/);
    } finally {
      rt.destroy();
    }
  });
});

/**
 * Batched prefill (6.4): a prompt fed in passes of `batch` tokens must give the
 * logits llama.cpp gives after its last token — at every prefix length, so every
 * position of a pass and every split between passes is checked. A batch of 5
 * makes a 40-token sequence eight passes, with a partial one wherever the length
 * is not a multiple of 5.
 */
describe('prefill vs llama.cpp', () => {
  for (const [name, url, expected, tolerance] of [
    ['all-F32 llama', f32Url, f32Expected, 1e-5],
    ['Q8_0 / Q4_0 / F16 llama', mixedUrl, mixedExpected, 2e-2],
    ['Qwen3 F32', qwen3Url, qwen3Expected, 1e-5],
    ['Qwen2 F32', qwen2Url, qwen2Expected, 1e-5],
    ['Gemma 3 F32', gemma3Url, gemma3Expected, GEMMA_TOLERANCE],
    ['Llama 3 Q4_K_M', llama3Url, llama3Expected, 1e-2],
    ['Qwen3 Q5_K_M', qwen3kUrl, qwen3kExpected, 1e-2],
  ] as const) {
    it(`${name}: logits after every prefix, in passes of 5 tokens`, async () => {
      const want = expected as Expected;
      const tokens = [...want.prompt, ...want.generated];
      const rt = await load(url, { batch: 5 });
      try {
        let worst = 0;
        for (let n = 2; n <= tokens.length; n++) {
          const row = await rt.forward(tokens.slice(0, n));
          worst = Math.max(worst, rowError(row, want.logits[n - 1]));
          expect(argmax(row), `top token after ${n} tokens`).toBe(argmax(want.logits[n - 1]));
        }
        expect(worst).toBeLessThan(tolerance);
      } finally {
        rt.destroy();
      }
    });
  }

  it('profile times every dispatch of a pass, by kind', async () => {
    const rt = await load(qwen3Url, { batch: 8 });
    try {
      for (const [mode, n] of [
        ['decode', 1],
        ['prefill', 8],
      ] as const) {
        const prof = await rt.profile(mode, 3, n);
        const kinds = prof.kinds.map((k) => k.kind);
        for (const kind of ['embed', 'attn_q', 'q_norm', 'rope_k', 'attention', 'ffn_down']) {
          expect(kinds, `${mode} has ${kind}`).toContain(kind);
        }
        // Two layers: one dispatch per layer for a per-layer kind; the head's own.
        expect(prof.kinds.find((k) => k.kind === 'attention')?.dispatches).toBe(2);
        expect(kinds).toContain('output_norm');
        expect(prof.totalMs).toBeGreaterThan(0);
      }
    } finally {
      rt.destroy();
    }
  });

  it('decode steps continue correctly from a prefilled cache', async () => {
    const want = f32Expected as Expected;
    const tokens = [...want.prompt, ...want.generated];
    const rt = await load(f32Url, { batch: 7 });
    try {
      // 20 tokens as three passes (7 + 7 + 6), then one decode step at a time.
      await rt.forward(tokens.slice(0, 20));
      for (let i = 20; i < tokens.length; i++) {
        const row = await rt.forward([tokens[i]], i);
        expect(rowError(row, want.logits[i])).toBeLessThan(1e-5);
      }
    } finally {
      rt.destroy();
    }
  });
});

/**
 * The default f16 KV cache (6.4) against the same f32-cache references: K and V
 * are rounded to f16 once, when stored, and everything else stays f32, so the
 * logits move by that rounding only. Measured: worst 0.07% of a row’s largest
 * logit on these fixtures, and the same greedy tokens.
 */
describe('f16 KV cache vs llama.cpp (f32 cache)', () => {
  for (const [name, url, expected] of [
    ['all-F32 llama', f32Url, f32Expected],
    ['Qwen3 F32', qwen3Url, qwen3Expected],
  ] as const) {
    it(`${name}: same greedy continuation, prefill and decode, logits within 0.5%`, async () => {
      const want = expected as Expected;
      const rt = await load(url, { kvCache: 'f16', batch: 5 });
      try {
        expect(rt.kvCache).toBe('f16');
        let logits = await rt.forward(want.prompt);
        const generated: number[] = [];
        for (let i = 0; i < want.generated.length; i++) {
          generated.push(argmax(logits));
          logits = await rt.forward([generated[i]], want.prompt.length + i);
        }
        expect(generated).toEqual(want.generated);
        const rows = await logitsAlong(rt, [...want.prompt, ...want.generated]);
        expect(Math.max(...rows.map((r, i) => rowError(r, want.logits[i])))).toBeLessThan(5e-3);
      } finally {
        rt.destroy();
      }
    });
  }
});
