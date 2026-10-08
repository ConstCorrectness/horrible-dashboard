import { describe, expect, it } from 'vitest';

import type { LayerLens } from '@horrible/webml';

import {
  compareRuns,
  guessScore,
  guessVerdict,
  layerP,
  pickPuzzles,
  puzzleCandidates,
  showToken,
  type ForcedRun,
} from '../lens-lab/model';
import type { LensToken } from '../lens-run';

const layer = (best: string, p: number, extra: { token: string; p: number }[] = []): LayerLens => ({
  norm: 1,
  entropy: 1,
  top: [{ token: best, p }, ...extra],
});

/** A token whose lens says `bests` at each layer. */
const tok = (token: string, bests: string[]): LensToken => ({
  token,
  p: 0.9,
  layers: bests.map((b) => layer(b, 0.5)),
});

describe('cells', () => {
  it('reads a token’s probability out of a layer’s top five, 0 when absent', () => {
    const l = layer('Paris', 0.6, [{ token: 'London', p: 0.2 }]);
    expect(layerP(l, 'London')).toBe(0.2);
    expect(layerP(l, 'Rome')).toBe(0);
    expect(layerP(undefined, 'Rome')).toBe(0);
  });

  it('makes whitespace visible and cuts long tokens', () => {
    expect(showToken(' a\n')).toBe('·a↵');
    expect(showToken('')).toBe('∅');
    expect(showToken('abcdefghijkl', 6)).toBe('abcde…');
  });
});

describe('the guessing game', () => {
  const tokens = [
    tok(' The', [' The', ' The', ' The']), // settles at 0: nothing to guess
    tok(' capital', ['x', ' capital', ' capital']), // 1
    tok(' ', ['x', 'y', ' ']), // whitespace: skipped
    tok(' Paris', ['x', 'y', ' Paris']), // 2
    tok(' won', ['x', ' won', 'other']), // never settles (runner-up): skipped
  ];

  it('offers only tokens that settle somewhere in the stack', () => {
    expect(puzzleCandidates(tokens)).toEqual([
      { index: 1, token: ' capital', settles: 1 },
      { index: 3, token: ' Paris', settles: 2 },
    ]);
  });

  it('spreads its picks across the reply, in order', () => {
    const many = Array.from({ length: 20 }, (_, i) => tok(`t${i}`, ['x', `t${i}`]));
    const picks = pickPuzzles(many, 5, () => 0);
    expect(picks.map((p) => p.index)).toEqual([0, 4, 8, 12, 16]);
    expect(pickPuzzles(many.slice(0, 3), 5)).toHaveLength(3);
  });

  it('scores 100 for the exact layer, nothing a quarter of the stack away', () => {
    expect(guessScore(10, 10, 28)).toBe(100);
    expect(guessScore(17, 10, 28)).toBe(0);
    expect(guessScore(12, 10, 28)).toBe(71);
    // Tiny stacks still give partial credit for being one off.
    expect(guessScore(1, 2, 2)).toBe(0);
    expect(guessScore(3, 4, 8)).toBe(50);
  });

  it('says how far off and which way', () => {
    expect(guessVerdict(5, 5)).toBe('Exactly');
    expect(guessVerdict(3, 5)).toBe('2 layers too early');
    expect(guessVerdict(6, 5)).toBe('1 layer too late');
  });
});

describe('compareRuns', () => {
  const run = (
    model: string,
    steps: { token: string; p: number; bests: string[] }[],
  ): ForcedRun => ({
    model,
    steps: steps.map((s) => ({
      token: s.token,
      p: s.p,
      entropy: 1,
      layers: s.bests.map((b) => layer(b, b === s.token ? 0.8 : 0.3)),
    })),
  });

  it('lines the two up by position and measures each difference', () => {
    const a = run('base', [
      { token: ' Paris', p: 0.5, bests: ['x', 'y', ' Paris'] },
      { token: '.', p: 0.25, bests: ['x', '.', '.'] },
    ]);
    const b = run('tuned', [
      { token: ' Paris', p: 0.9, bests: ['x', ' Paris', ' Paris'] },
      { token: '.', p: 0.25, bests: ['x', ',', 'other'] },
    ]);
    const c = compareRuns(a, b);
    expect(c.aligned).toBe(true);
    expect(c.layers).toBe(3);
    expect(c.positions.map((p) => [p.settleA, p.settleB])).toEqual([
      [2, 1],
      [1, null],
    ]);
    // Layer 1 of position 0: B says ' Paris' at 0.8, A does not have it at all.
    expect(c.positions[0].layerDelta[1]).toBeCloseTo(0.8);
    // Position 1's last layer: A 0.8 for '.', B does not have it.
    expect(c.positions[1].layerDelta[2]).toBeCloseTo(-0.8);
    // B's top at the output differs from A's only at position 1.
    expect(c.disagreements).toBe(1);
    expect(c.meanLogpB).toBeGreaterThan(c.meanLogpA);
    // Mean settle only over positions where both settled.
    expect([c.meanSettleA, c.meanSettleB]).toEqual([2, 1]);
  });

  it('notices when the models tokenized the text differently', () => {
    const a = run('a', [{ token: 'ab', p: 0.5, bests: ['ab'] }]);
    const b = run('b', [
      { token: 'a', p: 0.5, bests: ['a'] },
      { token: 'b', p: 0.5, bests: ['b'] },
    ]);
    const c = compareRuns(a, b);
    expect(c.aligned).toBe(false);
    expect(c.positions).toHaveLength(1);
  });
});
