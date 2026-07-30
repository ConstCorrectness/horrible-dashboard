/**
 * The cube world, geometry builder, and player physics.
 *
 * These import the sibling logic files directly rather than the module manifest:
 * the manifest pulls in React panels, and a core vitest run has no jsdom, so
 * importing it dies at module scope.
 */
import { describe, expect, it } from 'vitest';

import type { MapInfo } from '../api';
import { buildWorldMesh } from '../geometry';
import { canStand, createPlayer, spawnAt, step, STEP_HEIGHT } from '../player';
import { CHF, FHF, PLAYER_EYE_HEIGHT, SOLID, SPACE, World } from '../world';

const PLANES = ['type', 'floor', 'ceil', 'wtex', 'ftex', 'ctex', 'vdelta', 'utex', 'tag'];

interface CellSpec {
  type?: number;
  floor?: number;
  ceil?: number;
  vdelta?: number;
  wtex?: number;
  utex?: number;
}

/**
 * Build a synthetic world. Cells default to SOLID, so a test only describes the
 * open space it cares about.
 */
function makeWorld(ssize: number, cells: Record<string, CellSpec> = {}): World {
  const n = ssize * ssize;
  const buf = new ArrayBuffer(n * PLANES.length);
  const plane = (name: string) => {
    const off = PLANES.indexOf(name) * n;
    return name === 'floor' || name === 'ceil'
      ? new Int8Array(buf, off, n)
      : new Uint8Array(buf, off, n);
  };
  const type = plane('type');
  const floor = plane('floor');
  const ceil = plane('ceil');
  const vdelta = plane('vdelta');
  const wtex = plane('wtex');
  const utex = plane('utex');

  type.fill(SOLID);
  ceil.fill(16);

  for (const [key, spec] of Object.entries(cells)) {
    const [x, y] = key.split(',').map(Number);
    const i = y * ssize + x;
    type[i] = spec.type ?? SPACE;
    floor[i] = spec.floor ?? 0;
    ceil[i] = spec.ceil ?? 16;
    vdelta[i] = spec.vdelta ?? 0;
    wtex[i] = spec.wtex ?? 2;
    utex[i] = spec.utex ?? 2;
  }

  const info: MapInfo = {
    name: 'test',
    title: 'test',
    magic: 'ACMP',
    version: 10,
    sfactor: Math.log2(ssize),
    ssize,
    cubic_size: n,
    waterlevel: -100,
    watercolor: [0, 0, 0, 0],
    maprevision: 1,
    ambient: 0,
    flags: 0,
    timestamp: 0,
    entity_count: 0,
    entities: [],
    spawns: {},
    truncated: false,
    legacy_unscaled_attrs: false,
    plane_order: PLANES,
  };
  return new World(info, buf);
}

/**
 * A rectangle of open floor, which most tests want.
 *
 * `_ssize` is unused but kept in the signature so call sites read as
 * `openRoom(16, …)` and stay obviously tied to the world they belong to.
 */
function openRoom(
  _ssize: number,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  spec: CellSpec = {},
) {
  const cells: Record<string, CellSpec> = {};
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) cells[`${x},${y}`] = { ...spec };
  return cells;
}

describe('World', () => {
  it('slices planes by the order the backend reports', () => {
    const w = makeWorld(8, { '3,4': { type: SPACE, floor: 5, ceil: 12 } });
    expect(w.type[w.index(3, 4)]).toBe(SPACE);
    expect(w.floor[w.index(3, 4)]).toBe(5);
    expect(w.ceil[w.index(3, 4)]).toBe(12);
  });

  it('reads floor and ceil as signed', () => {
    const w = makeWorld(8, { '2,2': { floor: -13, ceil: -2 } });
    expect(w.floor[w.index(2, 2)]).toBe(-13);
    expect(w.ceil[w.index(2, 2)]).toBe(-2);
  });

  it('rejects a short cube payload rather than reading past the end', () => {
    const info: MapInfo = {
      ...makeWorld(8).info,
      ssize: 8,
      cubic_size: 64,
      plane_order: PLANES,
    };
    expect(() => new World(info, new ArrayBuffer(10))).toThrow(/expected/);
  });

  it('treats out of bounds as solid so the player cannot leave the map', () => {
    const w = makeWorld(8, openRoom(8, 2, 2, 5, 5));
    expect(w.isSolid(-1, 3)).toBe(true);
    expect(w.isSolid(3, 99)).toBe(true);
    expect(w.isSolid(3, 3)).toBe(false);
  });

  describe('heightfield corners', () => {
    it('takes the base height from the cell and the delta from the corner vertex', () => {
      // The bug this pins: reading the *base* from the corner cell instead of
      // the owning cell tears adjacent heightfields apart at every seam.
      const w = makeWorld(8, {
        '3,3': { type: FHF, floor: 10, vdelta: 0 },
        '4,3': { type: FHF, floor: 2, vdelta: 8 }, // different floor, big delta
      });
      // Cell (3,3) sampled at its +x corner uses *its own* floor of 10, with the
      // delta stored at vertex (4,3), which is 8 → 10 - 8/4 = 8.
      expect(w.cornerFloor(3, 3, 4, 3)).toBe(8);
      // The neighbour at the same vertex uses its own floor of 2 → 2 - 2 = 0.
      expect(w.cornerFloor(4, 3, 4, 3)).toBe(0);
    });

    it('applies vdelta/4 per vertex', () => {
      const w = makeWorld(8, { '3,3': { type: FHF, floor: 10, vdelta: 12 } });
      expect(w.cornerFloor(3, 3, 3, 3)).toBe(10 - 3);
    });

    it('ignores vdelta on a cell that is not a heightfield', () => {
      const w = makeWorld(8, { '3,3': { type: SPACE, floor: 10, vdelta: 12 } });
      expect(w.cornerFloor(3, 3, 3, 3)).toBe(10);
    });

    it('raises a CHF ceiling rather than lowering it', () => {
      const w = makeWorld(8, { '3,3': { type: CHF, ceil: 10, vdelta: 8 } });
      expect(w.cornerCeil(3, 3, 3, 3)).toBe(12);
    });

    it('averages the four corner deltas over 16 for the standing height', () => {
      // physics.cpp:287 — the mean of four vdelta/4 terms, i.e. sum/16. Using /4
      // here instead would sink the player into every slope.
      const w = makeWorld(8, {
        '3,3': { type: FHF, floor: 10, vdelta: 4 },
        '4,3': { type: FHF, floor: 10, vdelta: 8 },
        '3,4': { type: FHF, floor: 10, vdelta: 4 },
        '4,4': { type: FHF, floor: 10, vdelta: 0 },
      });
      expect(w.floorAt(3, 3)).toBe(10 - (4 + 8 + 4 + 0) / 16);
    });
  });
});

describe('buildWorldMesh', () => {
  it('emits a floor and a ceiling for each open cell', () => {
    const w = makeWorld(8, openRoom(8, 3, 3, 4, 4)); // 4 open cells
    const mesh = buildWorldMesh(w);
    // 4 cells × (floor + ceiling) = 8 quads = 16 triangles, plus the walls that
    // ring the room: 8 edges against solid = 8 quads = 16 triangles.
    expect(mesh.triangles).toBe(32);
    expect(mesh.positions.length).toBe(mesh.triangles * 9);
    expect(mesh.normals.length).toBe(mesh.positions.length);
    expect(mesh.colors.length).toBe(mesh.positions.length);
  });

  it('emits nothing for a fully solid world', () => {
    expect(buildWorldMesh(makeWorld(8)).triangles).toBe(0);
  });

  it('does not wall between two open cells at the same height', () => {
    const two = buildWorldMesh(makeWorld(8, openRoom(8, 3, 3, 4, 3))).triangles;
    const one = buildWorldMesh(makeWorld(8, openRoom(8, 3, 3, 3, 3))).triangles;
    // Two adjacent cells: 2×(floor+ceil)=8 tris, ringed by 6 wall edges=12 tris.
    expect(two).toBe(20);
    // One cell: floor+ceil=4 tris, 4 walls=8 tris.
    expect(one).toBe(12);
  });

  it('emits a step wall where an open neighbour has a higher floor', () => {
    const flat = buildWorldMesh(makeWorld(8, openRoom(8, 3, 3, 4, 3))).triangles;
    const stepped = buildWorldMesh(
      makeWorld(8, { '3,3': { floor: 0 }, '4,3': { floor: 4 } }),
    ).triangles;
    expect(stepped).toBe(flat + 2); // one extra quad
  });

  it('emits an overhang wall where an open neighbour has a lower ceiling', () => {
    const flat = buildWorldMesh(makeWorld(8, openRoom(8, 3, 3, 4, 3))).triangles;
    const overhung = buildWorldMesh(
      makeWorld(8, { '3,3': { ceil: 16 }, '4,3': { ceil: 10 } }),
    ).triangles;
    expect(overhung).toBe(flat + 2);
  });

  it('keeps every position finite', () => {
    const w = makeWorld(8, {
      '3,3': { type: FHF, floor: 2, vdelta: 9 },
      '4,3': { type: CHF, ceil: 12, vdelta: 7 },
    });
    const mesh = buildWorldMesh(w);
    expect(mesh.positions.every((v) => Number.isFinite(v))).toBe(true);
  });
});

describe('player', () => {
  it('cannot stand inside a solid cell', () => {
    const w = makeWorld(16, openRoom(16, 3, 3, 9, 9));
    expect(canStand(w, 6, 6, 0)).toBe(true);
    expect(canStand(w, 12, 12, 0)).toBe(false);
  });

  it('needs three cells of clearance, because the body is 2.2 cubes wide', () => {
    // PLAYER_RADIUS is 1.1, and collision uses the circle's AABB (as AC's own
    // `rectcollide` does). A one- or two-cell gap therefore does not admit a
    // player, which is easy to forget when hand-building a test world.
    const oneWide = makeWorld(16, openRoom(16, 5, 4, 5, 12));
    expect(canStand(oneWide, 5.5, 8, 0)).toBe(false);
    const threeWide = makeWorld(16, openRoom(16, 4, 4, 6, 12));
    expect(canStand(threeWide, 5.5, 8, 0)).toBe(true);
  });

  it('walks up a small step but not a tall one', () => {
    const w = makeWorld(24, {
      ...openRoom(24, 3, 3, 8, 10, { floor: 0 }),
      ...openRoom(24, 9, 3, 14, 10, { floor: STEP_HEIGHT - 0.5 }),
      ...openRoom(24, 15, 3, 20, 10, { floor: STEP_HEIGHT + 6 }),
    });
    expect(canStand(w, 11, 6, 0)).toBe(true);
    expect(canStand(w, 17, 6, 0)).toBe(false);
  });

  it('will not stand where the ceiling is too low', () => {
    const roomy = makeWorld(16, openRoom(16, 3, 3, 9, 9, { floor: 0, ceil: 16 }));
    const cramped = makeWorld(16, openRoom(16, 3, 3, 9, 9, { floor: 0, ceil: 2 }));
    expect(canStand(roomy, 6, 6, 0)).toBe(true);
    expect(canStand(cramped, 6, 6, 0)).toBe(false);
  });

  it('falls under gravity and lands on the floor', () => {
    const w = makeWorld(16, openRoom(16, 4, 4, 10, 10, { floor: 3 }));
    const p = createPlayer(7, 7, 20);
    for (let i = 0; i < 200; i++) {
      step(w, p, { forward: 0, strafe: 0, jump: false, crouch: false, noclip: false }, 1 / 60);
    }
    expect(p.z).toBeCloseTo(3, 5);
    expect(p.onGround).toBe(true);
  });

  it('slides along a wall instead of stopping dead', () => {
    // A corridor running along +y, walled at x = 7. Walking diagonally into that
    // wall must still make progress along y — resolving both axes together would
    // reject the whole move and make every corner sticky.
    const w = makeWorld(24, openRoom(24, 4, 4, 6, 20, { floor: 0 }));
    const p = createPlayer(5.5, 5.5, 0);
    p.yaw = Math.PI / 4; // diagonally into the +x wall
    const y0 = p.y;
    for (let i = 0; i < 30; i++) {
      step(w, p, { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false }, 1 / 60);
    }
    expect(p.y).toBeGreaterThan(y0 + 1);
    expect(p.x).toBeGreaterThan(5.5); // moved toward the wall…
    expect(p.x).toBeLessThan(6.5); // …but did not pass through it
  });

  it('clamps a huge timestep so a backgrounded tab cannot teleport the player', () => {
    const w = makeWorld(16, openRoom(16, 4, 4, 10, 10, { floor: 0 }));
    const p = createPlayer(7, 7, 0);
    step(w, p, { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false }, 60);
    // At 22 cubes/s a 60s step would cross the map; the clamp bounds it.
    expect(Math.abs(p.x - 7)).toBeLessThan(4);
  });

  it('noclip ignores walls', () => {
    const w = makeWorld(16, openRoom(16, 4, 4, 5, 5));
    const p = createPlayer(4.5, 4.5, 0);
    p.yaw = 0;
    for (let i = 0; i < 60; i++) {
      step(w, p, { forward: 1, strafe: 0, jump: false, crouch: false, noclip: true }, 1 / 60);
    }
    expect(p.x).toBeGreaterThan(10);
  });

  it('spawns on the ground even when the entity z sits inside the floor', () => {
    const w = makeWorld(16, openRoom(16, 4, 4, 10, 10, { floor: 6 }));
    const p = spawnAt(w, { x: 7, y: 7, z: 0, yaw: 90 });
    expect(p.z).toBe(6);
    expect(p.yaw).toBeCloseTo(Math.PI / 2, 6);
  });
});

describe('spawnAt', () => {
  /**
   * A `playerstart`'s `z` is the mapper's *eye* at placement time and AC's editor
   * flies, so it is not a ground height. Read as a lower bound — `max(floor, z)`,
   * which this used to do — it put all 1741 official spawns in mid-air, because
   * it is above the floor at all but six of them.
   */
  it('lands the feet on the ground however high the entity sits', () => {
    const w = makeWorld(16, openRoom(16, 4, 4, 10, 10, { floor: 3, ceil: 24 }));
    for (const z of [-20, 0, 3, 7, 40]) {
      expect(spawnAt(w, { x: 7, y: 7, z, yaw: 0 }).z).toBe(3);
    }
  });

  it('is the fixed point of the first simulated step', () => {
    const w = makeWorld(16, openRoom(16, 4, 4, 10, 10, { floor: 5, ceil: 24 }));
    const p = spawnAt(w, { x: 7, y: 7, z: 17, yaw: 0 });
    const before = { x: p.x, y: p.y, z: p.z };
    step(w, p, { forward: 0, strafe: 0, jump: false, crouch: false, noclip: false }, 1 / 60);
    expect(p.x).toBeCloseTo(before.x, 9);
    expect(p.y).toBeCloseTo(before.y, 9);
    expect(p.z).toBeCloseTo(before.z, 9);
    expect(p.onGround).toBe(true);
  });

  it('puts the eye under the ceiling', () => {
    // The old placement could leave it above: on ac_desert, feet at 12 with a
    // ceiling of 16 puts the eye at 16.5, which reads as solid to a raycast.
    const w = makeWorld(16, openRoom(16, 4, 4, 10, 10, { floor: 0, ceil: 16 }));
    const p = spawnAt(w, { x: 7, y: 7, z: 12, yaw: 0 });
    expect(p.z + PLAYER_EYE_HEIGHT).toBeLessThan(w.ceilAt(7, 7));
  });

  it('stands on the highest floor under the body, not the centre cell', () => {
    // The body is 2.2 cubes wide, so it straddles the step at x = 8.
    const cells = {
      ...openRoom(16, 4, 4, 10, 10, { floor: 0, ceil: 24 }),
      ...openRoom(16, 8, 4, 10, 10, { floor: 2, ceil: 24 }),
    };
    const w = makeWorld(16, cells);
    expect(spawnAt(w, { x: 7, y: 7, z: 40, yaw: 0 }).z).toBe(2);
  });

  it('treats a null entity yaw as zero', () => {
    const w = makeWorld(16, openRoom(16, 4, 4, 10, 10, { floor: 0 }));
    expect(spawnAt(w, { x: 7, y: 7, z: 0, yaw: null }).yaw).toBe(0);
  });

  it('still places a player sealed inside solid geometry', () => {
    // No official map manages it, but refusing would turn an odd community map
    // into an unjoinable one.
    const w = makeWorld(16, {});
    expect(Number.isFinite(spawnAt(w, { x: 7, y: 7, z: 30, yaw: 0 }).z)).toBe(true);
  });
});
