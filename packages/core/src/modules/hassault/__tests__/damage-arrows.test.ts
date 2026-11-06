import { describe, expect, it } from 'vitest';

import {
  ARROW_ALPHA,
  arrowAlpha,
  arrowTriangle,
  DAMAGE_ARROW_LIFE_MS,
  MAX_DAMAGE_ARROWS,
  pushHurt,
} from '../damage-arrows';

describe('damage arrows', () => {
  it('point at the shooter in all four quarters', () => {
    // Screen +y is down, so "ahead" is a negative y. The wrong sign points
    // every arrow at the exact opposite of the shooter and still looks like
    // working code.
    const yaw = 0.7;
    const tip = (rel: number) => arrowTriangle(yaw + rel, yaw, 4)[0];
    const ahead = tip(0);
    expect(ahead[1]).toBeLessThan(0);
    expect(Math.abs(ahead[0])).toBeLessThan(1e-9);
    expect(tip(Math.PI)[1]).toBeGreaterThan(0);
    // The world's bearings run the same way round as the noise ring's, and the
    // ring puts +pi/2 on the right.
    expect(tip(Math.PI / 2)[0]).toBeGreaterThan(0);
    expect(tip(-Math.PI / 2)[0]).toBeLessThan(0);
  });

  it('sit at the native radius in HUD units, tip outside the base', () => {
    const unit = 3;
    const [tip, left, right] = arrowTriangle(0, 0, unit);
    expect(Math.hypot(...tip)).toBeCloseTo(33 * unit, 9);
    expect(Math.hypot(...left)).toBeCloseTo(26 * unit, 9);
    expect(Math.hypot(...right)).toBeCloseTo(26 * unit, 9);
    expect(left[0]).toBeLessThan(0);
    expect(right[0]).toBeGreaterThan(0);
  });

  it('fade on a square curve and are gone at the end of their life', () => {
    expect(arrowAlpha(0)).toBeCloseTo(ARROW_ALPHA, 9);
    expect(arrowAlpha(DAMAGE_ARROW_LIFE_MS / 2)).toBeCloseTo(ARROW_ALPHA / 4, 9);
    expect(arrowAlpha(DAMAGE_ARROW_LIFE_MS)).toBe(0);
    expect(arrowAlpha(DAMAGE_ARROW_LIFE_MS * 3)).toBe(0);
  });

  it('arrive, expire and are capped keeping the newest', () => {
    let arrows = pushHurt([], [{ bearing: 1, amount: 20 }], 1000);
    expect(arrows).toHaveLength(1);
    arrows = pushHurt(arrows, undefined, 1000 + DAMAGE_ARROW_LIFE_MS + 1);
    expect(arrows).toHaveLength(0);
    // A shotgun's worth of pellets in one snapshot.
    const hurt = Array.from({ length: MAX_DAMAGE_ARROWS + 4 }, (_, i) => ({
      bearing: i * 0.1,
      amount: 9,
    }));
    arrows = pushHurt([], hurt, 5000);
    expect(arrows).toHaveLength(MAX_DAMAGE_ARROWS);
    expect(arrows[arrows.length - 1].bearing).toBeCloseTo((MAX_DAMAGE_ARROWS + 3) * 0.1, 9);
    expect(new Set(arrows.map((a) => a.id)).size).toBe(MAX_DAMAGE_ARROWS);
  });

  it('hand back the same list when nothing changed, so nothing re-renders', () => {
    const arrows = pushHurt([], [{ bearing: 0, amount: 5 }], 1000);
    expect(pushHurt(arrows, [], 1100)).toBe(arrows);
    expect(pushHurt(arrows, undefined, 1100)).toBe(arrows);
    const empty = pushHurt([], undefined, 0);
    expect(pushHurt(empty, undefined, 10)).toBe(empty);
  });

  it('ignore a marker with no usable bearing', () => {
    expect(pushHurt([], [{ bearing: Number.NaN, amount: 5 }], 0)).toHaveLength(0);
  });
});
