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
  MATERIAL_TINTS,
  decalOpacity,
  drawImpactTile,
  surfaceMaterial,
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

/**
 * A minimal fake world whose `wtex`, `ftex`, and `ctex` arrays can be filled
 * with specific texture ids per cell to test surface material detection.
 */
function fakeWorld(ssize: number) {
  return {
    ssize,
    wtex: new Uint8Array(ssize * ssize),
    ftex: new Uint8Array(ssize * ssize),
    ctex: new Uint8Array(ssize * ssize),
  };
}

describe('surfaceMaterial', () => {
  it('returns metal for known metal texture ids on a wall', () => {
    const world = fakeWorld(16);
    // Metal texture ids: 3, 21, 58, 89, 131, 150, 233
    for (const texId of [3, 21, 58, 89, 131, 150, 233]) {
      world.wtex[3 * 16 + 5] = texId;
      expect(surfaceMaterial(world, [5.5, 3.5, 1], FACE_PX)).toBe('metal');
    }
  });

  it('returns wood for known wood texture ids on a wall', () => {
    const world = fakeWorld(16);
    for (const texId of [4, 33, 76, 104, 214]) {
      world.wtex[4 * 16 + 7] = texId;
      expect(surfaceMaterial(world, [7.5, 4.5, 1], FACE_NX)).toBe('wood');
    }
  });

  it('returns glass for glass texture id', () => {
    const world = fakeWorld(16);
    world.wtex[2 * 16 + 1] = 199;
    expect(surfaceMaterial(world, [1.5, 2.5, 1], FACE_PY)).toBe('glass');
  });

  it('returns concrete for any other texture id', () => {
    const world = fakeWorld(16);
    // Texture ids 2, 6, 12, 47, 178 are explicitly concrete; 0 (default) also maps there
    for (const texId of [0, 2, 6, 12, 47, 178]) {
      world.wtex[5 * 16 + 5] = texId;
      expect(surfaceMaterial(world, [5.5, 5.5, 1], FACE_PX)).toBe('concrete');
    }
  });

  it('reads ftex for floor face (FACE_PZ)', () => {
    const world = fakeWorld(16);
    world.ftex[3 * 16 + 3] = 21; // metal
    world.wtex[3 * 16 + 3] = 0; // concrete on wall
    expect(surfaceMaterial(world, [3.5, 3.5, 1], FACE_PZ)).toBe('metal');
  });

  it('reads ctex for ceiling face (FACE_NZ)', () => {
    const world = fakeWorld(16);
    world.ctex[6 * 16 + 6] = 4; // wood
    world.wtex[6 * 16 + 6] = 0; // concrete on wall
    expect(surfaceMaterial(world, [6.5, 6.5, 2], FACE_NZ)).toBe('wood');
  });

  it('returns default for FACE_NONE', () => {
    const world = fakeWorld(16);
    expect(surfaceMaterial(world, [1, 1, 1], FACE_NONE)).toBe('default');
  });

  it('returns default for a null world', () => {
    expect(surfaceMaterial(null, [1, 1, 1], FACE_PX)).toBe('default');
  });

  it('returns concrete for out-of-bounds coordinates', () => {
    const world = fakeWorld(8);
    expect(surfaceMaterial(world, [-1, 0, 1], FACE_PX)).toBe('concrete');
    expect(surfaceMaterial(world, [0, 9, 1], FACE_PX)).toBe('concrete');
  });
});

describe('MATERIAL_TINTS', () => {
  it('has a tint for every expected material', () => {
    for (const mat of ['concrete', 'metal', 'wood', 'glass', 'default'] as const) {
      expect(MATERIAL_TINTS[mat]).toBeTypeOf('number');
      expect(MATERIAL_TINTS[mat]).toBeGreaterThan(0);
    }
  });

  it('distinguishes metal from concrete', () => {
    expect(MATERIAL_TINTS.metal).not.toBe(MATERIAL_TINTS.concrete);
  });
});
