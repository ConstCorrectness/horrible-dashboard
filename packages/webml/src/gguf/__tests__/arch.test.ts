import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { checkSupport } from '../arch';
import { readGgufHeader } from '../parse';
import { ropeTable } from '../rope';
import { bytesSource } from '../source';

const header = (url: URL) => readGgufHeader(bytesSource(new Uint8Array(readFileSync(url))));
const kernelFixture = (name: string) =>
  new URL(`../../../kernel-tests/fixtures/${name}`, import.meta.url);

describe('checkSupport', () => {
  it('builds the config of a llama model from its metadata', async () => {
    const support = checkSupport(await header(kernelFixture('tiny-llama-f32.gguf')));
    expect(support).toEqual({
      ok: true,
      config: {
        arch: 'llama',
        layers: 2,
        embd: 64,
        ffn: 96,
        heads: 4,
        kvHeads: 2,
        headDim: 16,
        ropeDims: 16,
        ropeBase: 10000,
        rope: 'norm',
        ropeFreqs: false,
        qkNorm: false,
        attentionScale: 0.25,
        rmsEps: Math.fround(1e-5),
        contextLength: 64,
        vocab: 64,
        tiedEmbeddings: false,
      },
    });
  });

  it('notices tied embeddings', async () => {
    const support = checkSupport(await header(kernelFixture('tiny-llama-mixed.gguf')));
    expect(support.ok && support.config.tiedEmbeddings).toBe(true);
  });

  it('refuses tensors whose shapes contradict the metadata', async () => {
    const h = await header(kernelFixture('tiny-llama-f32.gguf'));
    h.tensors.find((t) => t.name === 'blk.1.attn_k.weight')!.shape = [64, 64];
    expect(checkSupport(h)).toEqual({
      ok: false,
      reasons: ['blk.1.attn_k.weight is 64×64, but the metadata implies 64×32'],
    });
  });

  it('refuses an unsupported architecture by name', async () => {
    const h = await header(kernelFixture('tiny-llama-f32.gguf'));
    h.metadata['general.architecture'] = 'phi3';
    expect(checkSupport(h)).toEqual({
      ok: false,
      reasons: ['architecture "phi3" is not supported (supported: llama, qwen3)'],
    });
  });

  it('refuses tensors it would ignore, rather than running without them', async () => {
    const h = await header(kernelFixture('tiny-llama-f32.gguf'));
    h.tensors.push({ ...h.tensors[1], name: 'blk.0.attn_q.bias' });
    const support = checkSupport(h);
    expect(support.ok).toBe(false);
    expect(!support.ok && support.reasons).toEqual([
      'tensors this engine would ignore (so it would compute the wrong thing): blk.0.attn_q.bias',
    ]);
  });

  it('names every problem with a malformed qwen3 header', async () => {
    const support = checkSupport(
      await header(new URL('./fixtures/tiny-qwen3.gguf', import.meta.url)),
    );
    // The fixture pins the header reader, so it has only some of a block's tensors.
    expect(!support.ok && support.reasons).toEqual([
      'missing tensors: blk.0.attn_v.weight, blk.0.attn_output.weight, blk.0.ffn_norm.weight, ' +
        'blk.0.ffn_gate.weight, blk.0.attn_k_norm.weight',
    ]);
  });
});

describe('ropeTable', () => {
  it('starts at angle 0 and at the position for the first pair', () => {
    const t = ropeTable(4, 8, 10000);
    expect([t[0], t[1]]).toEqual([1, 0]);
    expect(t[3 * 8]).toBeCloseTo(Math.cos(3), 6);
    expect(t[3 * 8 + 1]).toBeCloseTo(Math.sin(3), 6);
  });

  it('multiplies theta down in float32, as ggml does', () => {
    const f = Math.fround;
    const scale = f(Math.pow(10000, f(-2 / 8)));
    const t = ropeTable(3000, 8, 10000);
    // Pair 3 at position 2999: three float32 multiplications from 2999.
    const theta = f(f(f(2999 * scale) * scale) * scale);
    expect(t[2999 * 8 + 6]).toBe(Math.fround(Math.cos(theta)));
  });
});
