/**
 * The browser's post chain (`post-chain.ts`): when it runs, and how big its
 * bloom levels are. The shader numbers themselves are pinned against the
 * native client's by `apps/native-fps/tests/browser_parity.rs`.
 */
import { describe, expect, it } from 'vitest';

import { aces, chainActive, inverseAces, KNEE, LEVELS, mipSizes, THRESHOLD } from '../post-chain';

describe('the post chain', () => {
  it('is skipped entirely while bloom and sharpening are both off', () => {
    expect(chainActive({ bloom: 0, sharpen: 0 })).toBe(false);
    expect(chainActive({ bloom: 0.5, sharpen: 0 })).toBe(true);
    expect(chainActive({ bloom: 0, sharpen: 0.3 })).toBe(true);
  });

  it('builds the bloom chain from half resolution down, never below a pixel', () => {
    const sizes = mipSizes(1920, 1080);
    expect(sizes).toHaveLength(LEVELS + 1);
    expect(sizes[0]).toEqual([960, 540]);
    expect(sizes[1]).toEqual([480, 270]);
    expect(sizes.at(-1)).toEqual([30, 16]);
    expect(mipSizes(3, 3).every(([w, h]) => w >= 1 && h >= 1)).toBe(true);
  });

  it('blooms only what is already near white, as native does', () => {
    expect(THRESHOLD).toBe(0.82);
    expect(KNEE).toBe(0.12);
  });

  it('pre-distorts the clear colour so tone mapping gives the canvas colour back', () => {
    // three never tone maps the background on the canvas; the chain tone maps
    // its whole target. Verified pixel-exact in a browser: sky and wall match.
    for (const sky of [
      [0.32, 0.45, 0.62],
      [0.05, 0.06, 0.08],
      [0.8, 0.7, 0.55],
    ] as [number, number, number][]) {
      for (const exposure of [0.9, 1.15, 1.6]) {
        const back = aces(inverseAces(sky, exposure), exposure);
        for (let i = 0; i < 3; i += 1) expect(back[i]).toBeCloseTo(sky[i], 4);
      }
    }
  });
});
