/**
 * Bullet marks.
 *
 * The pool itself needs three, so what is tested headless is everything that
 * decides *where* a mark goes and *whether* one goes at all — which is where the
 * failures are. A mark on the wrong face is inside its wall, and a mark drawn
 * for `FACE_NONE` is a hole in mid-air; neither throws, and neither is visible
 * enough to report itself.
 */
import { describe, expect, it } from 'vitest';

import {
  DECAL_FADE,
  DECAL_LIFE,
  DECAL_MAX,
  FACE_NORMALS,
  decalOpacity,
  drawImpactTile,
} from '../decals';
import {
  FACE_NONE,
  FACE_NX,
  FACE_NY,
  FACE_NZ,
  FACE_PX,
  FACE_PY,
  FACE_PZ,
} from '../trace';

const SIZE = 32;

describe('the face table', () => {
  it('has one unit normal per face, in the wire’s own order', () => {
    // Indexed by the integer the server puts on the wire, so the order is not a
    // convention this file may choose — it is `weapons.FACE_NORMALS`.
    expect(FACE_NORMALS).toHaveLength(6);
    expect(FACE_NORMALS[FACE_PX]).toEqual([1, 0, 0]);
    expect(FACE_NORMALS[FACE_NX]).toEqual([-1, 0, 0]);
    expect(FACE_NORMALS[FACE_PY]).toEqual([0, 1, 0]);
    expect(FACE_NORMALS[FACE_NY]).toEqual([0, -1, 0]);
    expect(FACE_NORMALS[FACE_PZ]).toEqual([0, 0, 1]);
    expect(FACE_NORMALS[FACE_NZ]).toEqual([0, 0, -1]);
    for (const n of FACE_NORMALS) {
      expect(Math.hypot(n[0], n[1], n[2])).toBeCloseTo(1, 12);
    }
  });

  it('cannot be indexed by FACE_NONE', () => {
    // Negative rather than a sixth value precisely so a caller that forgets to
    // check gets `undefined` rather than a mark quietly facing +x.
    expect(FACE_NONE).toBeLessThan(0);
    expect(FACE_NORMALS[FACE_NONE]).toBeUndefined();
  });
});

describe('the impact tile', () => {
  it('is empty at the rim', () => {
    // A decal with a visible edge reads as a sticker rather than as damage.
    const tile = drawImpactTile(SIZE);
    const half = SIZE / 2;
    const alpha = (px: number, py: number) => tile[(py * SIZE + px) * 4 + 3];
    expect(alpha(0, half)).toBe(0);
    expect(alpha(SIZE - 1, half)).toBe(0);
    expect(alpha(half, 0)).toBe(0);
    expect(alpha(half, SIZE - 1)).toBe(0);
  });

  it('is opaque at the crater', () => {
    const tile = drawImpactTile(SIZE);
    const half = SIZE / 2;
    expect(tile[(half * SIZE + half) * 4 + 3]).toBe(255);
  });

  it('is darkest at the crater and pale at its rim', () => {
    // What makes a hole read as a hole rather than as a dark circle.
    const tile = drawImpactTile(SIZE);
    const half = SIZE / 2;
    const value = (px: number, py: number) => tile[(py * SIZE + px) * 4];
    const centre = value(half, half);
    // ~0.38 of the radius out, inside the pale rim band.
    const rim = value(half + Math.round(half * 0.38), half);
    expect(rim).toBeGreaterThan(centre);
  });

  it('is deterministic', () => {
    expect(Array.from(drawImpactTile(SIZE))).toEqual(Array.from(drawImpactTile(SIZE)));
  });
});

describe('decalOpacity', () => {
  it('stays fully legible for most of a mark’s life', () => {
    // A mark that begins fading immediately is a mark that is never quite
    // readable, and reading a whole magazine's worth off a wall is the point.
    expect(decalOpacity(0)).toBe(1);
    expect(decalOpacity(DECAL_LIFE - DECAL_FADE)).toBe(1);
  });

  it('fades to nothing by the end of its life', () => {
    expect(decalOpacity(DECAL_LIFE - DECAL_FADE / 2)).toBeCloseTo(0.5, 6);
    expect(decalOpacity(DECAL_LIFE)).toBe(0);
    expect(decalOpacity(DECAL_LIFE * 10)).toBe(0);
  });

  it('never rises', () => {
    let previous = Infinity;
    for (let t = 0; t <= DECAL_LIFE + 1; t += 0.25) {
      const o = decalOpacity(t);
      expect(o).toBeLessThanOrEqual(previous);
      previous = o;
    }
  });
});

describe('the pool’s budget', () => {
  it('remembers a magazine’s worth of shots', () => {
    // The cap is a fixed ring buffer, so it holds by construction rather than by
    // a trim that has to run after every push. It has to be comfortably more
    // than one magazine or a player cannot see their own spray.
    expect(DECAL_MAX).toBeGreaterThanOrEqual(64);
    expect(DECAL_LIFE).toBeGreaterThan(DECAL_FADE);
  });
});
