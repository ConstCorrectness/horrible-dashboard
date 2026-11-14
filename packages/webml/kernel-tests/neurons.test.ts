/**
 * Neuron edits, activation capture and down-column norms (the Interp Suite's
 * Neurons tool) on the tiny models: an edit lands exactly where it says and
 * nowhere else, the cache never carries one recipe's positions into another, and
 * a down column read through the matvec matches the dequantized weights.
 */
import { describe, expect, it } from 'vitest';

import { tensor } from '../src/gguf/arch';
import { readGgufHeader } from '../src/gguf/parse';
import { dequantize } from '../src/gguf/quant';
import { GgufRuntime } from '../src/gguf/runtime';
import { editsKey, GgufSession, median } from '../src/gguf/session';
import { bytesSource } from '../src/gguf/source';
import f32Expected from './fixtures/tiny-llama-f32.expected.json';
import f32Url from './fixtures/tiny-llama-f32.gguf?url';
import qwen3kUrl from './fixtures/tiny-qwen3-q5km.gguf?url';
import { fixtureBytes, gpu } from './harness';

/** The fixture's tokens t2…t63 are user-defined, so a prompt is their texts run together. */
const text = (ids: number[]) => ids.map((i) => `t${i}`).join('');
const messages = [{ role: 'user' as const, content: text(f32Expected.prompt) }];

async function open(): Promise<GgufSession> {
  return GgufSession.open(await gpu(), bytesSource(await fixtureBytes(f32Url)));
}

async function greedy(
  s: GgufSession,
  neuronEdits?: Parameters<GgufSession['generate']>[0]['neuronEdits'],
) {
  const r = await s.generate(
    { messages, temperature: 0, maxNewTokens: f32Expected.generated.length, neuronEdits },
    {},
    () => false,
  );
  return r.text;
}

describe('neuron edits', () => {
  it('leave the reply untouched when empty, and never leak through the cache', async () => {
    const s = await open();
    try {
      const baseline = text(f32Expected.generated);
      expect(await greedy(s)).toBe(baseline);
      // A large push on every neuron of the middle layer changes the reply …
      const { ffn, layers } = s.shape;
      const mid = layers >> 1;
      const push = Array.from({ length: Math.min(ffn, 64) }, (_, index) => ({
        layer: mid,
        index,
        scale: 1,
        offset: 40,
      }));
      expect(await greedy(s, push)).not.toBe(baseline);
      // … and the same prompt afterwards, unedited, is the baseline again: the
      // steered prompt's cache entries were not reused.
      expect(await greedy(s)).toBe(baseline);
    } finally {
      s.destroy();
    }
  });

  it('ablate and offset exactly the neuron named, at its layer, and nothing earlier', async () => {
    const s = await open();
    try {
      const reply = text(f32Expected.generated);
      const at = (
        neuronEdits?: { layer: number; index: number; scale: number; offset: number }[],
      ) => s.captureActivations({ messages, reply, index: 3, neuronEdits });
      const base = await at();
      const { layers, ffn } = base;
      expect(base.activations).toHaveLength(layers * ffn);
      expect(base.predicted).toBe(`t${f32Expected.generated[3]}`);

      const layer = 1;
      const index = 5;
      const ablated = await at([{ layer, index, scale: 0, offset: 0 }]);
      const row = (c: Float32Array, l: number) => c.subarray(l * ffn, (l + 1) * ffn);
      // Layers before the edit see the same stream.
      expect(Array.from(row(ablated.activations, 0))).toEqual(Array.from(row(base.activations, 0)));
      // At the edited layer: that one neuron is zero, the others as they were.
      const got = row(ablated.activations, layer);
      const want = row(base.activations, layer);
      expect(got[index]).toBe(0);
      for (let i = 0; i < ffn; i++) if (i !== index) expect(got[i]).toBe(want[i]);

      const shifted = await at([{ layer, index, scale: 1, offset: 2.5 }]);
      expect(row(shifted.activations, layer)[index]).toBeCloseTo(want[index] + 2.5, 5);
    } finally {
      s.destroy();
    }
  });

  it('capture the same position whether it is prefilled or decoded', async () => {
    const s = await open();
    try {
      const reply = text(f32Expected.generated);
      // Index 0 is the prompt's last position, reached by the prompt's prefill.
      const prefilled = await s.captureActivations({ messages, reply, index: 0 });
      // Now prime the cache with that prompt so the next capture feeds one token.
      const decoded = await s.captureActivations({ messages, reply, index: 0 });
      const err = Math.max(
        ...prefilled.activations.map((v, i) => Math.abs(v - decoded.activations[i])),
      );
      expect(err).toBeLessThan(1e-4);
      expect(prefilled.position).toBe(f32Expected.prompt.length - 1);
    } finally {
      s.destroy();
    }
  });

  it('measures a positive residual median per layer', async () => {
    const s = await open();
    try {
      const medians = await s.residualMedians(
        text([...f32Expected.prompt, ...f32Expected.generated]),
      );
      expect(medians).toHaveLength(s.shape.layers);
      for (const m of medians) expect(m).toBeGreaterThan(0);
      // The session still generates the baseline afterwards.
      expect(await greedy(s)).toBe(text(f32Expected.generated));
    } finally {
      s.destroy();
    }
  });
});

describe('down-column norms', () => {
  for (const [name, url] of [
    ['f32', f32Url],
    ['q5_k', qwen3kUrl],
  ] as const) {
    it(`match the dequantized weights (${name})`, async () => {
      const bytes = await fixtureBytes(url);
      const source = bytesSource(bytes);
      const header = await readGgufHeader(source);
      const rt = await GgufRuntime.load(await gpu(), header, source, { kvCache: 'f32' });
      try {
        const layer = rt.config.layers - 1;
        const t = tensor(header, `blk.${layer}.ffn_down.weight`);
        const [cols, rows] = t.shape;
        const raw = await source.read(header.dataOffset + t.offset, t.bytes!);
        const w = dequantize(t.type, raw, 0, t.elements);
        for (const index of [0, 1, cols >> 1, cols - 1]) {
          let ss = 0;
          for (let r = 0; r < rows; r++) ss += w[r * cols + index] ** 2;
          expect(await rt.downColumnNorm(layer, index)).toBeCloseTo(Math.sqrt(ss), 4);
        }
      } finally {
        rt.destroy();
      }
    });
  }
});

describe('helpers', () => {
  it('median and editsKey', () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    const a = { layer: 1, index: 2, scale: 1, offset: 0.5 };
    const b = { layer: 0, index: 9, scale: 0, offset: 0 };
    expect(editsKey([a, b])).toBe(editsKey([b, a]));
    expect(editsKey([])).toBe('');
    expect(editsKey(undefined)).toBe('');
  });
});
