import { describe, expect, it } from 'vitest';

import {
  advance,
  locate,
  pacedSeconds,
  playheadAt,
  progressAt,
  revealed,
  segmentWeights,
} from '../replay';

describe('replay arithmetic', () => {
  it('locates a progress value in a round, clamped to the turn', () => {
    expect(locate(2.25, 4)).toEqual({ round: 2, fraction: 0.25 });
    expect(locate(-1, 4)).toEqual({ round: 0, fraction: 0 });
    expect(locate(9, 4)).toEqual({ round: 3, fraction: 1 });
    expect(locate(1, 0)).toEqual({ round: 0, fraction: 0 });
  });

  it('sizes rounds by wall time, filling gaps with the mean, never invisibly thin', () => {
    const w = segmentWeights([
      { wallMs: 3000, steps: 0 },
      { wallMs: 1000, steps: 0 },
      { wallMs: null, steps: 0 },
    ]);
    expect(w.reduce((a, b) => a + b, 0)).toBeCloseTo(1);
    // 3000 : 1000 : (mean 2000)
    expect(w[0] / w[1]).toBeCloseTo(3);
    expect(w[2] / w[1]).toBeCloseTo(2);
    const tiny = segmentWeights([
      { wallMs: 100000, steps: 0 },
      { wallMs: 1, steps: 0 },
    ]);
    expect(tiny[1]).toBeGreaterThan(0.15);
  });

  it('maps progress to the playhead and back', () => {
    const w = [0.5, 0.25, 0.25];
    expect(playheadAt(0, w)).toBe(0);
    expect(playheadAt(0.5, w)).toBeCloseTo(0.25);
    expect(playheadAt(1.5, w)).toBeCloseTo(0.625);
    expect(playheadAt(3, w)).toBeCloseTo(1);
    for (const t of [0, 0.3, 1.5, 2.9]) expect(progressAt(playheadAt(t, w), w)).toBeCloseTo(t);
  });

  it('reveals a round’s steps one by one, all of them before it ends', () => {
    expect(revealed(3, 0)).toBe(0);
    expect(revealed(3, 0.12)).toBe(1);
    expect(revealed(3, 0.82)).toBe(3);
    expect(revealed(3, 1)).toBe(3);
    expect(revealed(0, 0.5)).toBe(0);
    const counts = Array.from({ length: 21 }, (_, i) => revealed(4, i / 20));
    expect(counts).toEqual([...counts].sort((a, b) => a - b));
  });

  it('advances through paced rounds, faster at higher speed, stopping at the end', () => {
    const rounds = [
      { wallMs: null, steps: 0 },
      { wallMs: null, steps: 2 },
    ];
    expect(pacedSeconds(rounds[0])).toBeCloseTo(1.6);
    expect(pacedSeconds({ wallMs: null, steps: 100 })).toBe(6);
    expect(advance(0, 0.8, 1, rounds)).toBeCloseTo(0.5);
    expect(advance(0, 0.4, 2, rounds)).toBeCloseTo(0.5);
    // Crosses into round 1 (2.5s long): 1.6s finishes round 0, 1.25s is half of round 1.
    expect(advance(0, 2.85, 1, rounds)).toBeCloseTo(1.5);
    expect(advance(1.5, 100, 1, rounds)).toBe(2);
  });
});
