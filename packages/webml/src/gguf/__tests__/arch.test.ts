import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { checkSupport, layerWindow, type ModelConfig } from '../arch';
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
        qkvBias: false,
        postNorms: false,
        ffnAct: 'silu',
        embedScale: 1,
        slidingWindow: 0,
        swaPattern: 1,
        ropeBaseSwa: 10000,
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
      reasons: ['architecture "phi3" is not supported (supported: llama, qwen2, qwen3, gemma3)'],
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

  it('reads qwen2: NEOX rope and a bias on each of Q, K and V', async () => {
    const support = checkSupport(await header(kernelFixture('tiny-qwen2-f32.gguf')));
    expect(support.ok && support.config).toMatchObject({
      arch: 'qwen2',
      rope: 'neox',
      qkvBias: true,
      qkNorm: false,
      postNorms: false,
    });
  });

  it('refuses a qwen2 file missing a bias, rather than running without it', async () => {
    const h = await header(kernelFixture('tiny-qwen2-f32.gguf'));
    h.tensors = h.tensors.filter((t) => t.name !== 'blk.1.attn_v.bias');
    expect(checkSupport(h)).toEqual({ ok: false, reasons: ['missing tensors: blk.1.attn_v.bias'] });
  });

  it('reads gemma3: scaled embedding, post-norms, GELU, the window and both rope bases', async () => {
    const support = checkSupport(await header(kernelFixture('tiny-gemma3-f32.gguf')));
    expect(support.ok && support.config).toMatchObject({
      arch: 'gemma3',
      rope: 'neox',
      qkNorm: true,
      postNorms: true,
      ffnAct: 'gelu',
      embedScale: 8,
      slidingWindow: 5,
      swaPattern: 6,
      ropeBase: 1e6,
      ropeBaseSwa: 10000,
      headDim: 32,
      // 1/√headDim: llama.cpp ignores attention.scale for Gemma 3.
      attentionScale: 1 / Math.sqrt(32),
      rmsEps: Math.fround(1e-6),
    });
  });

  it('refuses logit soft-capping and a per-layer window pattern', async () => {
    const h = await header(kernelFixture('tiny-gemma3-f32.gguf'));
    h.metadata['gemma3.final_logit_softcapping'] = 30;
    h.metadata['gemma3.attention.sliding_window_pattern'] = [true, false] as never;
    expect(checkSupport(h)).toEqual({
      ok: false,
      reasons: [
        'final_logit_softcapping is not supported yet',
        'a per-layer attention.sliding_window_pattern is not supported yet',
      ],
    });
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

describe('layerWindow', () => {
  const config = (slidingWindow: number, swaPattern: number) =>
    ({ slidingWindow, swaPattern }) as ModelConfig;

  it('windows all but the last layer of each pattern, as llama.cpp’s set_swa_pattern', () => {
    const windows = Array.from({ length: 13 }, (_, l) => layerWindow(config(512, 6), l));
    expect(windows).toEqual([512, 512, 512, 512, 512, 0, 512, 512, 512, 512, 512, 0, 512]);
  });

  it('windows no layer when the model has no window', () => {
    expect(layerWindow(config(0, 6), 0)).toBe(0);
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
