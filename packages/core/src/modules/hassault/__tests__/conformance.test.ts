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
import { applyImpulse, createPlayer, spawnAt, step, type PlayerState } from '../player';
import { aimVector, BODY_HEIGHT, raycastWorld, rayHitsBody, type Vec } from '../trace';
import { PLAYER_RADIUS, SOLID, SPACE, World } from '../world';

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
    steps: {
      forward?: number;
      strafe?: number;
      jump?: boolean;
      crouch?: boolean;
      yaw?: number;
      dt: number;
      /** An external kick, applied after the step — where the match server
       * applies weapon recoil. See `applyImpulse`. */
      impulse?: [number, number, number];
    }[];
    expect: {
      x: number;
      y: number;
      z: number;
      velX: number;
      velY: number;
      velZ: number;
      crouch: number;
      onGround: boolean;
    };
  }[];
  spawns: {
    name: string;
    world: string;
    entity: { x: number; y: number; z: number; yaw?: number };
    expect: { x: number; y: number; z: number; yaw: number; onGround: boolean };
  }[];
  traces: {
    name: string;
    world: string;
    origin: Vec;
    yaw: number;
    pitch: number;
    max_distance: number;
    expect: number;
  }[];
  bodies: {
    name: string;
    origin: Vec;
    yaw: number;
    pitch: number;
    feet: Vec;
    height?: number;
    /** `null` is a clean miss, which is a result and not an absent one. */
    expect: number | null;
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
      // Built through `createPlayer` so a field added to `PlayerState` gets its
      // real default here rather than `undefined`, which would silently poison
      // the whole replay with NaN.
      const player: PlayerState = createPlayer(
        testCase.start.x as number,
        testCase.start.y as number,
        testCase.start.z as number,
        (testCase.start.yaw as number) ?? 0,
      );
      player.velX = (testCase.start.vel_x as number) ?? 0;
      player.velY = (testCase.start.vel_y as number) ?? 0;
      player.velZ = (testCase.start.vel_z as number) ?? 0;
      player.pitch = (testCase.start.pitch as number) ?? 0;
      player.onGround = (testCase.start.on_ground as boolean) ?? false;
      player.crouch = (testCase.start.crouch as number) ?? 0;
      for (const raw of testCase.steps) {
        if (raw.yaw !== undefined) player.yaw = raw.yaw;
        step(
          world,
          player,
          {
            forward: raw.forward ?? 0,
            strafe: raw.strafe ?? 0,
            jump: raw.jump ?? false,
            crouch: raw.crouch ?? false,
            noclip: false,
          },
          raw.dt,
        );
        // After the step, matching where the match server applies weapon recoil
        // (`simulate` steps, then `_handle_combat` fires).
        if (raw.impulse) applyImpulse(player, raw.impulse[0], raw.impulse[1], raw.impulse[2]);
      }
      const tol = vectors.tolerance;
      const digits = -Math.log10(tol);
      expect(player.x).toBeCloseTo(testCase.expect.x, digits);
      expect(player.y).toBeCloseTo(testCase.expect.y, digits);
      expect(player.z).toBeCloseTo(testCase.expect.z, digits);
      expect(player.velX).toBeCloseTo(testCase.expect.velX, digits);
      expect(player.velY).toBeCloseTo(testCase.expect.velY, digits);
      expect(player.velZ).toBeCloseTo(testCase.expect.velZ, digits);
      expect(player.crouch).toBeCloseTo(testCase.expect.crouch, digits);
      expect(player.onGround).toBe(testCase.expect.onGround);
    });
  }
});

/**
 * Spawn placement is one rule with two implementations, exactly like `step`, so
 * it is pinned the same way. A disagreement about where a player starts is a
 * desync from the very first frame.
 */
describe('cross-language spawn conformance', () => {
  it('has spawn vectors to check', () => {
    expect(vectors.spawns.length).toBeGreaterThan(0);
  });

  for (const testCase of vectors.spawns) {
    it(testCase.name, () => {
      const world = buildWorld(vectors.worlds[testCase.world]);
      const placed = spawnAt(world, { ...testCase.entity, yaw: testCase.entity.yaw ?? 0 });
      const digits = -Math.log10(vectors.tolerance);
      expect(placed.x).toBeCloseTo(testCase.expect.x, digits);
      expect(placed.y).toBeCloseTo(testCase.expect.y, digits);
      expect(placed.z).toBeCloseTo(testCase.expect.z, digits);
      expect(placed.yaw).toBeCloseTo(testCase.expect.yaw, digits);
      expect(placed.onGround).toBe(testCase.expect.onGround);
    });
  }
});

/**
 * Shot geometry, now that the training range traces its own shots.
 *
 * `trace.ts` is a third duplicate of rules the backend already implements, and
 * the DDA in it is a dozen lines where an off-by-one on a cell boundary stops
 * shots a fraction early — which nothing reports, and which would quietly teach
 * a player the wrong thing about their own aim. So it is pinned like the rest.
 */
describe('cross-language shot trace conformance', () => {
  it('has trace vectors to check', () => {
    expect(vectors.traces.length).toBeGreaterThan(0);
  });

  for (const testCase of vectors.traces) {
    it(testCase.name, () => {
      const world = buildWorld(vectors.worlds[testCase.world]);
      const distance = raycastWorld(
        world,
        testCase.origin,
        aimVector(testCase.yaw, testCase.pitch),
        testCase.max_distance,
      );
      expect(distance).toBeCloseTo(testCase.expect, -Math.log10(vectors.tolerance));
    });
  }
});

describe('cross-language body hit conformance', () => {
  it('has body vectors to check', () => {
    expect(vectors.bodies.length).toBeGreaterThan(0);
  });

  for (const testCase of vectors.bodies) {
    it(testCase.name, () => {
      const hit = rayHitsBody(
        testCase.origin,
        aimVector(testCase.yaw, testCase.pitch),
        testCase.feet,
        PLAYER_RADIUS,
        testCase.height ?? BODY_HEIGHT,
      );
      if (testCase.expect === null) {
        expect(hit).toBeNull();
      } else {
        expect(hit).not.toBeNull();
        expect(hit as number).toBeCloseTo(testCase.expect, -Math.log10(vectors.tolerance));
      }
    });
  }
});
