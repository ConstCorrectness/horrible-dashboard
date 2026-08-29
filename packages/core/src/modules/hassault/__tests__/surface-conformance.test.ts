/**
 * How the world is coloured, replayed against the vectors the native client
 * replays too.
 *
 * docs/architecture/hassault-two-clients.mdx kept two rows in its "not pinned"
 * table with a note that they were the ones most worth pinning next: the surface
 * tint per texture id (`geometry.ts` `texColor` / `geometry.rs` `tex_color`) and
 * the generated detail tile (`surfaces.ts` / `detail.rs`). This is that fixture.
 *
 * They were the right two. Both are pure functions of small integer inputs, so
 * the vectors are cheap; both decide what the *entire world* looks like, so a
 * drift is expensive; and neither has any failure signal at all — nothing
 * throws, no build breaks, the same map is simply a different colour or a
 * different grain depending on which client you launched, and the person who
 * notices is a player.
 *
 * As everywhere else on that page, the fixture pins **agreement**. What argues
 * the rules are right is `geometry.test.ts` here and the unit tests in each Rust
 * module there.
 *
 * ## Regenerating
 *
 * The expectations come from the browser, because the browser is where both
 * originated — `detail.rs`'s own header calls itself a port of `surfaces.ts`. To
 * rewrite them after a deliberate change to the look:
 *
 *     UPDATE_SURFACE_VECTORS=1 pnpm --filter @horrible/core test surface-conformance
 *
 * Then change both clients in the same commit and make both suites pass. The
 * generation lives here rather than in `scripts/` — where the physics generator
 * lives — for a boring reason worth writing down: the physics fixture is written
 * by Python, while these two functions are TypeScript with extensionless
 * imports, which bare Node cannot resolve. Vitest is the tool in this repo that
 * can read the browser's own source, and a generator carrying its own copy of
 * the maths would pin the copy rather than the client.
 */
import { readFileSync, writeFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { SHADE_CEIL, SHADE_FLOOR, SHADE_WALL_X, SHADE_WALL_Y, texColor } from '../geometry';
import { DETAIL_NEUTRAL, drawDetailTile } from '../surfaces';

const SHADES: Record<string, number> = {
  floor: SHADE_FLOOR,
  ceil: SHADE_CEIL,
  wallX: SHADE_WALL_X,
  wallY: SHADE_WALL_Y,
};

/**
 * Texture ids to tint.
 *
 * `wtex` is a byte, so 0 and 255 are the real ends of the range rather than
 * arbitrary large numbers — and the top of it is exactly where the two clients
 * are most likely to disagree, because the native side multiplies by the golden
 * ratio conjugate at `f32` precision while this side has the `f64` digits. 3 is
 * here because `geometry.rs` hardcodes it as `LADDER_TEX`.
 */
const TEX_IDS = [0, 1, 2, 3, 4, 7, 12, 31, 64, 100, 127, 128, 200, 254, 255];

/**
 * Pixels of the detail tile to pin.
 *
 * A whole 128x128 tile as JSON would be a 16k-entry array nobody can read the
 * diff of, and it would fail as one opaque blob rather than naming what moved.
 * These cover what can actually break: the four corners and the four edge
 * midpoints (the seam), the exact centre (grain with no seam at all), and a
 * scatter across the three octaves.
 */
const SAMPLES: [number, number][] = [
  [0, 0],
  [127, 0],
  [0, 127],
  [127, 127],
  [64, 0],
  [0, 64],
  [64, 127],
  [127, 64],
  [64, 64],
  [1, 1],
  [3, 96],
  [17, 5],
  [31, 31],
  [42, 77],
  [63, 65],
  [80, 12],
  [96, 96],
  [111, 34],
  [126, 126],
];

const SIZE = 128;

/**
 * A second, tiny tile.
 *
 * `size` is a parameter and the noise's wrap period is derived from it, so a
 * port that hardcoded 128 anywhere inside the lattice would still match every
 * sample above and fail only here.
 */
const SMALL_SIZE = 16;

interface Tint {
  tex: number;
  face: string;
  shade: number;
  rgb: [number, number, number];
}

interface Pixel {
  x: number;
  y: number;
  value: number;
}

interface Vectors {
  why: string;
  shades: Record<string, number>;
  tints: Tint[];
  detail: {
    neutralByte: number;
    size: number;
    pixels: Pixel[];
    smallSize: number;
    smallPixels: Pixel[];
    stats: { min: number; max: number; mean: number };
  };
}

/** Compute the whole fixture from the browser's own implementations. */
function compute(): Vectors {
  const tints: Tint[] = [];
  for (const tex of TEX_IDS) {
    for (const [face, shade] of Object.entries(SHADES)) {
      const rgb: [number, number, number] = [0, 0, 0];
      texColor(tex, shade, rgb);
      tints.push({ tex, face, shade, rgb });
    }
  }
  // The floor face at 0.95 of full shade: `buildWorldMesh` dims a cell whose
  // floor is raised, so this is a shade the world genuinely draws and would
  // otherwise go unpinned.
  for (const tex of [0, 3, 128, 255]) {
    const rgb: [number, number, number] = [0, 0, 0];
    const shade = SHADE_FLOOR * 0.95;
    texColor(tex, shade, rgb);
    tints.push({ tex, face: 'floorRaised', shade, rgb });
  }

  const tile = drawDetailTile(SIZE);
  const pixels = SAMPLES.map(([x, y]) => ({ x, y, value: tile[(y * SIZE + x) * 4] }));

  const small = drawDetailTile(SMALL_SIZE);
  const smallPixels: Pixel[] = [];
  for (let y = 0; y < SMALL_SIZE; y += 5) {
    for (let x = 0; x < SMALL_SIZE; x += 5) {
      smallPixels.push({ x, y, value: small[(y * SMALL_SIZE + x) * 4] });
    }
  }

  const stats = tileStats(tile);

  return {
    why: 'Generated by surface-conformance.test.ts with UPDATE_SURFACE_VECTORS=1 - do not hand-edit.',
    shades: SHADES,
    tints,
    detail: {
      neutralByte: Math.round(DETAIL_NEUTRAL * 255),
      size: SIZE,
      pixels,
      smallSize: SMALL_SIZE,
      smallPixels,
      // Whole-tile statistics, so a port that matched every sampled pixel by
      // luck and drifted everywhere else still fails.
      stats,
    },
  };
}

/** Min, max and mean of the red channel across a whole tile. */
function tileStats(tile: Uint8Array): { min: number; max: number; mean: number } {
  let min = 255;
  let max = 0;
  let sum = 0;
  for (let i = 0; i < tile.length; i += 4) {
    const v = tile[i];
    if (v < min) min = v;
    if (v > max) max = v;
    sum += v;
  }
  return { min, max, mean: sum / (tile.length / 4) };
}

const path = new URL('./surface-vectors.json', import.meta.url);

if (process.env.UPDATE_SURFACE_VECTORS) {
  writeFileSync(path, `${JSON.stringify(compute(), null, 2)}\n`);
}

// Read rather than imported, so the path is obviously the same file the Rust
// tests name — the same note as on the physics and clip vectors.
const vectors = JSON.parse(readFileSync(path, 'utf-8')) as Vectors;

describe('surface vectors', () => {
  it('covers both ends of the texture byte and every face shade', () => {
    // A fixture that has quietly stopped covering its inputs still passes every
    // case in it, which is the way a conformance file dies.
    const ids = new Set(vectors.tints.map((t) => t.tex));
    expect(ids.has(0)).toBe(true);
    expect(ids.has(255)).toBe(true);
    const faces = [...new Set(vectors.tints.map((t) => t.face))].sort();
    expect(faces).toEqual(['ceil', 'floor', 'floorRaised', 'wallX', 'wallY']);
  });

  it('names the shade constants the tints were computed at', () => {
    // The tint takes a shade, so pinning the colours while letting a constant
    // move would pin agreement on colours neither client ever draws.
    expect(vectors.shades).toEqual(SHADES);
  });
});

describe('surface tint', () => {
  for (const { tex, face, shade, rgb } of vectors.tints) {
    it(`tints texture ${tex} on a ${face} face`, () => {
      const out: [number, number, number] = [0, 0, 0];
      texColor(tex, shade, out);
      // Six decimals: this side is f64 and the native side is f32, so the
      // tolerance has to admit an f32 round trip while staying far tighter than
      // any difference an eye could find — a whole hue step is ~0.6 apart.
      expect(out[0]).toBeCloseTo(rgb[0], 6);
      expect(out[1]).toBeCloseTo(rgb[1], 6);
      expect(out[2]).toBeCloseTo(rgb[2], 6);
    });
  }
});

describe('detail tile', () => {
  const tile = drawDetailTile(vectors.detail.size);

  it('writes the neutral byte the material compensates for', () => {
    // `DETAIL_NEUTRAL` is the reciprocal the material multiplies by. If the
    // stored neutral and the compensation disagree, every surface in the game is
    // uniformly too dark or too bright, which reads as a lighting bug.
    expect(Math.round(DETAIL_NEUTRAL * 255)).toBe(vectors.detail.neutralByte);
  });

  for (const { x, y, value } of vectors.detail.pixels) {
    it(`writes ${value} at (${x}, ${y})`, () => {
      expect(tile[(y * vectors.detail.size + x) * 4]).toBe(value);
    });
  }

  it('is greyscale and opaque', () => {
    // Not a colour: a detail map carrying its own hue would make every surface
    // in every map the same colour, which is the flatness it exists to remove.
    for (let i = 0; i < tile.length; i += 4) {
      expect(tile[i + 1]).toBe(tile[i]);
      expect(tile[i + 2]).toBe(tile[i]);
      expect(tile[i + 3]).toBe(255);
    }
  });

  it('holds its whole-tile statistics', () => {
    const stats = tileStats(tile);
    expect(stats.min).toBe(vectors.detail.stats.min);
    expect(stats.max).toBe(vectors.detail.stats.max);
    expect(stats.mean).toBeCloseTo(vectors.detail.stats.mean, 6);
  });

  it('derives its wrap period from the size it was asked for', () => {
    const small = drawDetailTile(vectors.detail.smallSize);
    for (const { x, y, value } of vectors.detail.smallPixels) {
      expect(small[(y * vectors.detail.smallSize + x) * 4]).toBe(value);
    }
  });
});
