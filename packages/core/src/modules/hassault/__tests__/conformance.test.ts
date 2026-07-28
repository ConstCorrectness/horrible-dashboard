/**
 * The browser's physics, replayed against the same vectors the server replays.
 *
 * `backend/modules/hassault/physics.py` is a port of `player.ts` and `world.ts`,
 * because an authoritative match server has to be able to simulate. Two
 * implementations of one set of rules drift, and a drifted match does not throw —
 * it just puts each player somewhere the other cannot see. So both sides replay
 * `physics-vectors.json` and both must land in the same place.
 *
 * The fixture pins agreement, not correctness. What argues the rules are *right*
 * is `world.test.ts` here and the unit tests in
 * `backend/tests/test_hassault_physics.py` there.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import type { MapInfo } from '../api';
import { step, type PlayerState } from '../player';
import { SOLID, SPACE, World } from '../world';

const PLANES = ['type', 'floor', 'ceil', 'wtex', 'ftex', 'ctex', 'vdelta', 'utex', 'tag'];

interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  type?: number;
  floor?: number;
  ceil?: number;
  vdelta?: number;
}

interface WorldSpec {
  ssize: number;
  rects: Rect[];
}

interface Vectors {
  tolerance: number;
  worlds: Record<string, WorldSpec>;
  cases: {
    name: string;
    world: string;
    start: Record<string, number | boolean>;
    steps: { forward?: number; strafe?: number; jump?: boolean; yaw?: number; dt: number }[];
    expect: { x: number; y: number; z: number; velZ: number; onGround: boolean };
  }[];
}

// Read rather than import: no tsconfig JSON-resolution flag to depend on, and the
// path is then obviously the same file the Python suite names.
const vectors = JSON.parse(
  readFileSync(new URL('./physics-vectors.json', import.meta.url), 'utf-8'),
) as Vectors;

/** Mirrored by `build_world` in the Python suite. Everything starts SOLID. */
function buildWorld(spec: WorldSpec): World {
  const { ssize } = spec;
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
  type.fill(SOLID);
  ceil.fill(16);

  for (const rect of spec.rects) {
    for (let y = rect.y0; y <= rect.y1; y++) {
      for (let x = rect.x0; x <= rect.x1; x++) {
        const i = y * ssize + x;
        type[i] = rect.type ?? SPACE;
        floor[i] = rect.floor ?? 0;
        ceil[i] = rect.ceil ?? 16;
        vdelta[i] = rect.vdelta ?? 0;
      }
    }
  }

  const info: MapInfo = {
    name: 'conformance',
    title: 'conformance',
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

describe('cross-language physics conformance', () => {
  it('has vectors to check', () => {
    expect(vectors.cases.length).toBeGreaterThan(0);
  });

  for (const testCase of vectors.cases) {
    it(testCase.name, () => {
      const world = buildWorld(vectors.worlds[testCase.world]);
      const player: PlayerState = {
        x: testCase.start.x as number,
        y: testCase.start.y as number,
        z: testCase.start.z as number,
        velZ: (testCase.start.vel_z as number) ?? 0,
        yaw: (testCase.start.yaw as number) ?? 0,
        pitch: (testCase.start.pitch as number) ?? 0,
        onGround: (testCase.start.on_ground as boolean) ?? false,
      };
      for (const raw of testCase.steps) {
        if (raw.yaw !== undefined) player.yaw = raw.yaw;
        step(
          world,
          player,
          {
            forward: raw.forward ?? 0,
            strafe: raw.strafe ?? 0,
            jump: raw.jump ?? false,
            noclip: false,
          },
          raw.dt,
        );
      }
      const tol = vectors.tolerance;
      expect(player.x).toBeCloseTo(testCase.expect.x, -Math.log10(tol));
      expect(player.y).toBeCloseTo(testCase.expect.y, -Math.log10(tol));
      expect(player.z).toBeCloseTo(testCase.expect.z, -Math.log10(tol));
      expect(player.velZ).toBeCloseTo(testCase.expect.velZ, -Math.log10(tol));
      expect(player.onGround).toBe(testCase.expect.onGround);
    });
  }
});
