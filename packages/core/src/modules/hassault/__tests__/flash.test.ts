/**
 * The muzzle flash.
 *
 * There is no reference image to compare a generated texture against, so what is
 * pinned here is the handful of properties that make it *not* the thing it
 * replaced: a five-sided cone seen end-on, filling the view, one shot at a time.
 *
 * Those properties are (1) the tile fades to nothing at its rim — a hard edge is
 * the pentagon wearing a different outline — and (2) the size is capped as a
 * fraction of the screen, computed against the FOV the camera has *now*, since
 * scoping divides it.
 */
import { describe, expect, it } from 'vitest';

import {
  FLASH_LIFE,
  FLASH_MAX_SCREEN_FRACTION,
  FLASH_SMOKE_LIFE,
  clampFlashScale,
  drawFlashTile,
  drawSmokeTile,
  flashScale,
  flashShape,
  haloScale,
  smokePuff,
} from '../flash';

const SIZE = 32;

function alphaAt(tile: Uint8Array, size: number, px: number, py: number): number {
  return tile[(py * size + px) * 4 + 3];
}

describe('the flash tile', () => {
  it('is brightest at the centre and empty at the rim', () => {
    const tile = drawFlashTile(SIZE);
    const half = SIZE / 2;
    expect(alphaAt(tile, SIZE, half, half)).toBeGreaterThan(200);
    // The four rim midpoints. A tile that is still lit here draws a hard-edged
    // disc, which is exactly the failure the sprite exists to avoid.
    expect(alphaAt(tile, SIZE, 0, half)).toBe(0);
    expect(alphaAt(tile, SIZE, SIZE - 1, half)).toBe(0);
    expect(alphaAt(tile, SIZE, half, 0)).toBe(0);
    expect(alphaAt(tile, SIZE, half, SIZE - 1)).toBe(0);
  });

  it('falls off monotonically along a ray from the centre', () => {
    const tile = drawFlashTile(SIZE);
    const half = SIZE / 2;
    // Along +x from the centre, where the spoke term is constant, so any rise is
    // the falloff itself being wrong rather than a spoke crossing.
    let previous = Infinity;
    for (let px = half; px < SIZE; px++) {
      const a = alphaAt(tile, SIZE, px, half);
      expect(a).toBeLessThanOrEqual(previous);
      previous = a;
    }
  });

  it('is deterministic', () => {
    // Procedural, not random: two view models in one session must not hold two
    // different guns' worth of flash.
    expect(Array.from(drawFlashTile(SIZE))).toEqual(Array.from(drawFlashTile(SIZE)));
    expect(Array.from(drawSmokeTile(SIZE))).toEqual(Array.from(drawSmokeTile(SIZE)));
  });

  it('writes white RGB, so the colour is the material’s', () => {
    const tile = drawFlashTile(SIZE);
    for (let i = 0; i < tile.length; i += 4) {
      expect(tile[i]).toBe(255);
      expect(tile[i + 1]).toBe(255);
      expect(tile[i + 2]).toBe(255);
    }
  });
});

describe('flashScale', () => {
  it('gives each weapon its own size', () => {
    const sizes = ['pistol', 'assault', 'shotgun', 'sniper'].map((id) => flashScale(id, 0, 1));
    expect(new Set(sizes).size).toBe(4);
    // A shotgun blooms widest, which is the cue that says which of two shapes at
    // the end of a corridor just fired at you.
    expect(flashShape('shotgun')!.radius).toBeGreaterThan(flashShape('pistol')!.radius);
  });

  it('gives the knife no flash at all', () => {
    // Not an omission: a swing resolves as a `Shot` like everything else, and a
    // flare would light up the one weapon whose value is that carrying it gives
    // nothing away. The native client has always refused it.
    expect(flashShape('knife')).toBeNull();
    expect(flashScale('knife', 0, 1)).toBe(0);
    expect(haloScale('knife', 0.2)).toBe(0);
    expect(smokePuff('knife', 0).opacity).toBe(0);
  });

  it('shrinks over the flash’s life rather than flickering', () => {
    // The old code re-rolled `Math.random()` every lit frame, so one shot was
    // two or three unrelated sizes. One shot is now one decay.
    const a = flashScale('assault', 0, 7);
    const b = flashScale('assault', FLASH_LIFE * 0.5, 7);
    const c = flashScale('assault', FLASH_LIFE, 7);
    expect(a).toBeGreaterThan(b);
    expect(b).toBeGreaterThan(c);
  });

  it('varies between shots, and repeats for one shot', () => {
    expect(flashScale('pistol', 0, 1)).not.toBeCloseTo(flashScale('pistol', 0, 2), 6);
    expect(flashScale('pistol', 0, 1)).toBe(flashScale('pistol', 0, 1));
  });

  it('draws the halo wider than the core', () => {
    const core = flashScale('sniper', 0, 3);
    expect(haloScale('sniper', core)).toBeGreaterThan(core);
  });
});

describe('clampFlashScale', () => {
  const fov = (60 * Math.PI) / 180;

  it('leaves a small flash alone', () => {
    expect(clampFlashScale(0.05, 1.2, fov)).toBeCloseTo(0.05, 9);
  });

  it('caps a flash at the stated fraction of the viewport height', () => {
    const distance = 1.2;
    const capped = clampFlashScale(99, distance, fov);
    const viewHeight = Math.tan(fov / 2) * distance * 2;
    expect(capped / viewHeight).toBeCloseTo(FLASH_MAX_SCREEN_FRACTION, 9);
  });

  it('is more permissive further from the eye', () => {
    // The same fraction of the screen is a larger object further away, so a cap
    // that did not move with distance would be a cap on the wrong quantity.
    expect(clampFlashScale(99, 2.4, fov)).toBeGreaterThan(clampFlashScale(99, 1.2, fov));
  });

  it('tightens as the FOV narrows — the scoped case', () => {
    // The bug this exists for: the sniper divides its FOV by the magnification,
    // so a cap computed against the base FOV is four times too generous at 4x.
    const hip = clampFlashScale(99, 1.2, fov);
    const scoped = clampFlashScale(99, 1.2, fov / 4);
    expect(scoped).toBeLessThan(hip);
  });

  it('declines to divide by nothing', () => {
    expect(clampFlashScale(0.2, 0, fov)).toBe(0.2);
    expect(clampFlashScale(0.2, 1, 0)).toBe(0.2);
  });
});

describe('the smoke wisp', () => {
  it('outlives the flash', () => {
    expect(FLASH_SMOKE_LIFE).toBeGreaterThan(FLASH_LIFE);
  });

  it('grows as it fades, and is gone by the end', () => {
    const early = smokePuff('assault', 0);
    const late = smokePuff('assault', FLASH_SMOKE_LIFE * 0.8);
    expect(late.scale).toBeGreaterThan(early.scale);
    expect(late.opacity).toBeLessThan(early.opacity);
    expect(smokePuff('assault', FLASH_SMOKE_LIFE).opacity).toBe(0);
  });

  it('is never bright enough to read as a second flash', () => {
    for (let t = 0; t <= FLASH_SMOKE_LIFE; t += FLASH_SMOKE_LIFE / 20) {
      expect(smokePuff('shotgun', t).opacity).toBeLessThan(0.3);
    }
  });
});
