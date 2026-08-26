/**
 * The world mesh, and the two things baked into it.
 *
 * `geometry.ts` is pure — it never imports three — so the whole mesh can be
 * built and inspected with no canvas and no WebGL context. That is the only
 * reason lighting work in this module is testable at all, and it is worth using:
 * an occlusion bug has no error, no warning and no failing frame. It just looks
 * slightly wrong in a way nobody can point at.
 *
 * What is pinned here is what a screenshot cannot check quickly: that a corner
 * is darker than the middle of the room, that the darkening is bounded, and that
 * the UVs stay in cube units so a detail texture keeps one scale everywhere.
 */
import { describe, expect, it } from 'vitest';

import { buildWorldMesh } from '../geometry';
import { drawDetailTile, DETAIL_NEUTRAL } from '../surfaces';
import { SOLID, SPACE, World } from '../world';

/** A world of `ssize` cells, solid everywhere, with a two-cube border. */
function makeWorld(ssize: number): World {
  const n = ssize * ssize;
  const planes = ['type', 'floor', 'ceil', 'wtex', 'ftex', 'ctex', 'vdelta', 'utex', 'tag'];
  const buffer = new ArrayBuffer(n * planes.length);
  const info = {
    name: 'test',
    ssize,
    cubic_size: n,
    plane_order: planes,
    entities: [],
  } as unknown as ConstructorParameters<typeof World>[0];
  const world = new World(info, buffer);
  world.type.fill(SOLID);
  world.ceil.fill(8);
  return world;
}

/** Carve an open box from `(x0, y0)` to `(x1, y1)` inclusive. */
function carve(world: World, x0: number, y0: number, x1: number, y1: number): void {
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const i = world.index(x, y);
      world.type[i] = SPACE;
      world.floor[i] = 0;
      world.ceil[i] = 8;
    }
  }
}

interface Vertex {
  x: number;
  y: number;
  z: number;
  /** Red channel, which stands in for brightness: the tint is per texture id
   * and constant within one surface, so only the occlusion varies. */
  r: number;
  u: number;
  v: number;
  /** The face's normal, which is the only way to tell a floor vertex from the
   * foot of the wall standing on it — they share a position. */
  ny: number;
  nx: number;
  nz: number;
}

function vertices(world: World): Vertex[] {
  const data = buildWorldMesh(world);
  const out: Vertex[] = [];
  for (let i = 0; i < data.positions.length / 3; i++) {
    out.push({
      x: data.positions[i * 3],
      y: data.positions[i * 3 + 1],
      z: data.positions[i * 3 + 2],
      r: data.colors[i * 3],
      u: data.uvs[i * 2],
      v: data.uvs[i * 2 + 1],
      nx: data.normals[i * 3],
      ny: data.normals[i * 3 + 1],
      nz: data.normals[i * 3 + 2],
    });
  }
  return out;
}

const isFloor = (p: Vertex) => p.ny === 1;
const isWall = (p: Vertex) => p.ny === 0;

/** The brightness of the floor vertex at a grid point. */
function floorBrightnessAt(verts: Vertex[], x: number, z: number): number {
  const hits = verts.filter((p) => isFloor(p) && p.x === x && p.z === z);
  expect(hits.length, `no floor vertex at ${x},${z}`).toBeGreaterThan(0);
  // Every quad meeting this grid vertex computes the same occlusion for it, so
  // any of them is the answer.
  return hits[0].r;
}

describe('ambient occlusion', () => {
  // One room, 8x8 open cells at (4,4)..(11,11), walls all round it.
  const world = makeWorld(20);
  carve(world, 4, 4, 11, 11);
  const verts = vertices(world);

  it('darkens a floor corner more than a floor edge, and an edge more than the middle', () => {
    // This is the whole point of the feature: three depths of shading inside one
    // flat, single-coloured floor, which is what tells the eye where the walls
    // are before any texture exists.
    const corner = floorBrightnessAt(verts, 4, 4);
    const edge = floorBrightnessAt(verts, 4, 8);
    const middle = floorBrightnessAt(verts, 8, 8);

    expect(corner).toBeLessThan(edge);
    expect(edge).toBeLessThan(middle);
  });

  it('leaves an unoccluded surface at its full colour', () => {
    // AO must be a multiplier that reaches exactly 1, or every surface in every
    // map is dimmed by a constant and the maps all read as underlit.
    const middle = floorBrightnessAt(verts, 8, 8);
    const brightest = Math.max(...verts.filter(isFloor).map((p) => p.r));
    expect(middle).toBeCloseTo(brightest, 6);
  });

  it('never darkens a surface to black', () => {
    // A crease with no texture and no light left in it is a hole in the level.
    const darkest = Math.min(...verts.map((p) => p.r));
    expect(darkest).toBeGreaterThan(0.05);
  });

  it('creases the foot of a step but not its top', () => {
    // A step up stands in open air, so its bottom edge meets a floor and its top
    // edge meets nothing. That asymmetry is the wall rule doing its job: a
    // version that shaded both ends the same would put a dark line along the lip
    // of every staircase in the game.
    //
    // A full-height wall is deliberately *not* the case tested here — it runs
    // floor to ceiling by construction, so both its ends are creases and the two
    // are supposed to match.
    const stepped = makeWorld(20);
    carve(stepped, 4, 4, 11, 11);
    for (let y = 8; y <= 11; y++) {
      for (let x = 4; x <= 11; x++) stepped.floor[stepped.index(x, y)] = 2;
    }
    const stepVerts = vertices(stepped);
    // The riser faces -z, out of the low half of the room, and spans 0..2.
    const riser = stepVerts.filter((p) => isWall(p) && p.nz === -1 && p.z === 8);
    const foot = riser.filter((p) => p.y === 0);
    const top = riser.filter((p) => p.y === 2);
    expect(foot.length).toBeGreaterThan(0);
    expect(top.length).toBeGreaterThan(0);
    const avg = (a: Vertex[]) => a.reduce((sum, p) => sum + p.r, 0) / a.length;
    expect(avg(foot)).toBeLessThan(avg(top));
  });
});

describe('uvs', () => {
  const world = makeWorld(20);
  carve(world, 4, 4, 11, 11);
  const data = buildWorldMesh(world);

  it('emits one uv per vertex', () => {
    expect(data.uvs.length / 2).toBe(data.positions.length / 3);
  });

  it('measures in cube units, so a detail texture keeps one scale', () => {
    // A UV that ran 0..1 across each face would stretch the same tile over a
    // 1-cube step and a 20-cube wall, and the world would have no consistent
    // sense of size — which is the specific thing a detail texture is for.
    const floor = vertices(world).filter(isFloor);
    expect(floor.length).toBeGreaterThan(0);
    for (const p of floor) {
      expect(p.u).toBeCloseTo(p.x, 6);
      expect(p.v).toBeCloseTo(p.z, 6);
    }
  });
});

describe('detail tile', () => {
  const size = 32;
  const tile = drawDetailTile(size);

  it('stays near neutral, so it modulates rather than repaints', () => {
    // The mean has to sit close to the neutral value the material compensates
    // for. If it drifts, every surface in the game changes brightness and the
    // cause is a texture nobody is looking at.
    let sum = 0;
    for (let i = 0; i < size * size; i++) sum += tile[i * 4];
    const mean = sum / (size * size) / 255;
    expect(mean).toBeGreaterThan(DETAIL_NEUTRAL * 0.85);
    expect(mean).toBeLessThan(DETAIL_NEUTRAL * 1.05);
  });

  it('can brighten as well as darken', () => {
    // A multiplier that only ever subtracts is a dirt map, and it drags the
    // whole world dark however carefully the lighting is set.
    const values = [];
    for (let i = 0; i < size * size; i++) values.push(tile[i * 4] / 255);
    expect(Math.max(...values)).toBeGreaterThan(DETAIL_NEUTRAL);
    expect(Math.min(...values)).toBeLessThan(DETAIL_NEUTRAL);
  });

  it('is deterministic', () => {
    // The same map must look the same every time it loads. A random tile would
    // mean a wall you know changing appearance between rounds.
    expect(Array.from(drawDetailTile(size))).toEqual(Array.from(tile));
  });

  it('is opaque everywhere', () => {
    // It is used as a `map` on an opaque material; a stray alpha would punch
    // holes in the level.
    for (let i = 0; i < size * size; i++) expect(tile[i * 4 + 3]).toBe(255);
  });
});
