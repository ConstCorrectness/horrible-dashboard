/**
 * The GPU sampler (sample.wgsl) against the CPU one (gguf/sample.ts), which is
 * llama.cpp's default chain: same token for the same random number, same
 * distribution reported. f32 on the GPU against f64 in JS, so probabilities agree
 * to ~1e-6, and a draw could only differ when its random number sits within that
 * of a boundary — not in these seeded cases.
 */
import { describe, expect, it } from 'vitest';

import { DEFAULT_SAMPLER, sample, seededRandom, type SamplerOptions } from '../src/gguf/sample';
import { Kernels } from '../src/gpu/kernels';
import { gpu, output, seeded, upload } from './harness';

const RECORD_WORDS = 4 + 2 * 64;

interface GpuSampled {
  id: number;
  token: number;
  p: number;
  entropy: number;
  top: { id: number; p: number }[];
}

async function sampleOnGpu(
  logits: Float32Array,
  options: SamplerOptions,
  random: number,
  record: number,
): Promise<GpuSampled> {
  const { device } = await gpu();
  const k = new Kernels(device);
  const tokens = output(device, 4, 'tokens');
  const rec = output(device, RECORD_WORDS, 'record');
  const params = new ArrayBuffer(32);
  const u = new Uint32Array(params);
  const f = new Float32Array(params);
  const greedy = options.temperature <= 0;
  u.set([logits.length, greedy ? 0 : options.topK, record, greedy ? 1 : 0]);
  f.set([options.temperature, options.topP, options.minP, random], 4);
  const uniform = device.createBuffer({
    size: 32,
    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
  });
  device.queue.writeBuffer(uniform, 0, params);

  const pipeline = k.sample();
  const encoder = device.createCommandEncoder();
  const pass = encoder.beginComputePass();
  pass.setPipeline(pipeline);
  pass.setBindGroup(
    0,
    device.createBindGroup({
      layout: pipeline.getBindGroupLayout(0),
      entries: [upload(device, logits), tokens, rec, uniform].map((buffer, binding) => ({
        binding,
        resource: { buffer },
      })),
    }),
  );
  pass.dispatchWorkgroups(1);
  pass.end();
  const staging = device.createBuffer({
    size: (RECORD_WORDS + 1) * 4,
    usage: GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST,
  });
  encoder.copyBufferToBuffer(rec, 0, staging, 0, RECORD_WORDS * 4);
  encoder.copyBufferToBuffer(tokens, 0, staging, RECORD_WORDS * 4, 4);
  device.queue.submit([encoder.finish()]);
  await staging.mapAsync(GPUMapMode.READ);
  const words = new Uint32Array(staging.getMappedRange().slice(0));
  staging.destroy();
  const floats = new Float32Array(words.buffer);
  const top = [];
  for (let j = 0; j < words[3]; j++) top.push({ id: words[4 + 2 * j], p: floats[5 + 2 * j] });
  return { id: words[0], token: words[RECORD_WORDS], p: floats[1], entropy: floats[2], top };
}

/** The CPU sampler's answer, its zero-probability alternatives dropped. */
function sampleOnCpu(
  logits: Float32Array,
  options: SamplerOptions,
  random: number,
  record: number,
) {
  const { id, dist } = sample(logits, options, () => random, record);
  return {
    id,
    p: dist ? dist.probs[id] : 0,
    entropy: dist?.entropy ?? 0,
    top: (dist?.top ?? []).filter((t) => t.p > 0),
  };
}

function expectSame(got: GpuSampled, want: ReturnType<typeof sampleOnCpu>, label: string) {
  expect(got.id, `${label}: id`).toBe(want.id);
  expect(got.token, `${label}: id written to the token buffer`).toBe(want.id);
  expect(got.p, `${label}: p`).toBeCloseTo(want.p, 5);
  expect(got.entropy, `${label}: entropy`).toBeCloseTo(want.entropy, 4);
  expect(
    got.top.map((t) => t.id),
    `${label}: alternatives`,
  ).toEqual(want.top.map((t) => t.id));
  got.top.forEach((t, i) =>
    expect(t.p, `${label}: p of alternative ${i}`).toBeCloseTo(want.top[i].p, 5),
  );
}

/** Logits shaped like a model's: noise, scaled, with a few strong candidates. */
function logitsLike(vocab: number, seed: number, spread: number, peaks: number): Float32Array {
  const l = seeded(vocab, seed).map((v) => v * spread);
  const pick = seededRandom(seed + 1);
  for (let i = 0; i < peaks; i++) l[Math.floor(pick() * vocab)] += 4 + 6 * pick();
  return l;
}

describe('GPU sampler vs gguf/sample.ts', () => {
  it('greedy: the argmax, with the full softmax’s top alternatives and entropy', async () => {
    const logits = logitsLike(50_000, 3, 3, 8);
    const options = { ...DEFAULT_SAMPLER, temperature: 0 };
    expectSame(
      await sampleOnGpu(logits, options, 0.5, 5),
      sampleOnCpu(logits, options, 0.5, 5),
      'greedy',
    );
    // No alternatives asked for: still the argmax.
    const bare = await sampleOnGpu(logits, options, 0.5, 0);
    expect(bare.id).toBe(sampleOnCpu(logits, options, 0.5, 0).id);
    expect(bare.top).toEqual([]);
  });

  it('temperature draws: the same token for the same random number, typical logits', async () => {
    const random = seededRandom(11);
    for (let i = 0; i < 40; i++) {
      const logits = logitsLike(8_000, 100 + i, 2, 1 + (i % 6));
      const options = { ...DEFAULT_SAMPLER, temperature: [0.3, 0.8, 1.5][i % 3] };
      const r = random();
      expectSame(
        await sampleOnGpu(logits, options, r, 4),
        sampleOnCpu(logits, options, r, 4),
        `case ${i}`,
      );
    }
  });

  it('one dominant token: fewer than k within reach of the max', async () => {
    const logits = seeded(30_000, 9);
    logits[1234] = 40;
    const options = { ...DEFAULT_SAMPLER, temperature: 0.8 };
    for (const r of [0.01, 0.5, 0.999]) {
      const got = await sampleOnGpu(logits, options, r, 3);
      expectSame(got, sampleOnCpu(logits, options, r, 3), `r=${r}`);
      expect(got.id).toBe(1234);
    }
  });

  it('flat logits: more than a thousand candidates near the max, still the exact top-k', async () => {
    // Every logit within 1 of the max: the first count is the whole vocabulary.
    const logits = seeded(60_000, 21).map((v) => v * 0.5);
    for (const [temperature, topP, minP] of [
      [0.8, 0.95, 0.05],
      [1.0, 1.0, 0.0],
    ]) {
      const options = { temperature, topK: 40, topP, minP };
      for (const r of [0.2, 0.7]) {
        expectSame(
          await sampleOnGpu(logits, options, r, 10),
          sampleOnCpu(logits, options, r, 10),
          `T=${temperature} r=${r}`,
        );
      }
    }
  });
});
