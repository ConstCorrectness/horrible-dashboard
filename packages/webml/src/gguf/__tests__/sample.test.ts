import { describe, expect, it } from 'vitest';

import { DEFAULT_SAMPLER, sample, seededRandom } from '../sample';

const logits = Float32Array.from([1, 5, 3, -2, 4.5, 0]);

describe('sample', () => {
  it('is argmax at temperature 0', () => {
    expect(sample(logits, { ...DEFAULT_SAMPLER, temperature: 0 }).id).toBe(1);
  });

  it('only ever draws from the top-k', () => {
    const random = seededRandom(1);
    const seen = new Set<number>();
    for (let i = 0; i < 500; i++) {
      seen.add(sample(logits, { temperature: 5, topK: 2, topP: 1, minP: 0 }, random).id);
    }
    expect([...seen].sort()).toEqual([1, 4]);
  });

  it('applies min-p relative to the most likely token', () => {
    // p(5) / p(4.5) = e^0.5 ≈ 1.65, p(3)/p(5) = e^-2 ≈ 0.135: min-p 0.2 keeps two.
    const random = seededRandom(2);
    const seen = new Set<number>();
    for (let i = 0; i < 500; i++) {
      seen.add(sample(logits, { temperature: 1, topK: 0, topP: 1, minP: 0.2 }, random).id);
    }
    expect([...seen].sort()).toEqual([1, 4]);
  });

  it('applies top-p over the cumulative distribution', () => {
    const peaked = Float32Array.from([10, 0, 0, 0]);
    const random = seededRandom(3);
    for (let i = 0; i < 100; i++) {
      expect(sample(peaked, { temperature: 1, topK: 0, topP: 0.9, minP: 0 }, random).id).toBe(0);
    }
  });

  it('draws in proportion to softmax(logit / temperature)', () => {
    const two = Float32Array.from([0, Math.log(3)]);
    const random = seededRandom(4);
    let ones = 0;
    const n = 20_000;
    for (let i = 0; i < n; i++) {
      if (sample(two, { temperature: 1, topK: 0, topP: 1, minP: 0 }, random).id === 1) ones++;
    }
    expect(ones / n).toBeGreaterThan(0.73);
    expect(ones / n).toBeLessThan(0.77);
  });

  it('records the distribution it drew from, with removed tokens at 0', () => {
    const { dist } = sample(
      logits,
      { temperature: 2, topK: 3, topP: 1, minP: 0 },
      seededRandom(5),
      3,
    );
    expect(dist!.top.map((t) => t.id)).toEqual([1, 4, 2]);
    expect(dist!.probs[0]).toBe(0);
    expect(dist!.top.reduce((s, t) => s + t.p, 0)).toBeCloseTo(1, 6);
    // Temperature 2 flattens: p(5)/p(4.5) = e^(0.25).
    expect(dist!.top[0].p / dist!.top[1].p).toBeCloseTo(Math.exp(0.25), 5);
  });
});
