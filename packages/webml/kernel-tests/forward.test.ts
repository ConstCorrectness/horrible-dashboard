/**
 * The 6.1 and 6.3 exit checks, runnable without the Hub: whole forward passes
 * through the WGSL engine against llama.cpp on the same GGUF files
 * (scripts/gen_webml_llama_parity.py). The K-quant files were made by llama.cpp's
 * own quantizer, as real Q4_K_M / Q5_K_M files are.
 */
import { describe, expect, it } from 'vitest';

import { readGgufHeader } from '../src/gguf/parse';
import { GgufRuntime, type LoadOptions } from '../src/gguf/runtime';
import { bytesSource } from '../src/gguf/source';
import { gpu } from './harness';
import f32Expected from './fixtures/tiny-llama-f32.expected.json';
import f32Url from './fixtures/tiny-llama-f32.gguf?url';
import mixedExpected from './fixtures/tiny-llama-mixed.expected.json';
import mixedUrl from './fixtures/tiny-llama-mixed.gguf?url';
import llama3Expected from './fixtures/tiny-llama3-q4km.expected.json';
import llama3Url from './fixtures/tiny-llama3-q4km.gguf?url';
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

async function load(url: string, options: LoadOptions = {}): Promise<GgufRuntime> {
  const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer());
  const source = bytesSource(bytes);
  return GgufRuntime.load(await gpu(), await readGgufHeader(source), source, options);
}

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
    const rt = await load(f32Url, { contextLength: 16 });
    try {
      expect(rt.context).toBe(16);
      expect(() => rt.queueStep(3, 16)).toThrow(/outside the 16-token context/);
      expect(() => rt.queueStep(64, 0)).toThrow(/outside the 64-token vocabulary/);
    } finally {
      rt.destroy();
    }
  });
});
