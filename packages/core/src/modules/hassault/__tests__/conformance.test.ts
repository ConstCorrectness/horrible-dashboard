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

import { DEFAULT_HITBOX } from '../hitbox';
import { applyImpulse, createPlayer, spawnAt, step, type PlayerState } from '../player';
import type { WeaponSpec } from '../api';
import { buildWorld, type WorldSpec } from './build-world';
import {
  aimVector,
  applySpray,
  BODY_HEIGHT,
  raycastWorldFace,
  rayHitsBody,
  residualSpread,
  sprayOffset,
  type Vec,
} from '../trace';
import { PLAYER_RADIUS } from '../world';


interface Vectors {
  tolerance: number;
  hitboxSpecId: string;
  hitbox: Record<string, string | number>;
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
    /**
     * Which surface stopped the ray, as an index into `FACE_NORMALS`.
     *
     * `null` where the geometry has no answer — a ray fired at exactly 45° from
     * a cell corner crosses both boundaries at once, and which face wins is
     * decided by the last bit of `cos(yaw)`, which Python and V8 do not agree
     * on. The distance is still pinned; only the face is dropped.
     */
    face: number | null;
  }[];
  /** The served weapon table, verbatim from `Weapon.to_dict`. */
  weapons: Record<string, WeaponSpec>;
  sprays: {
    name: string;
    weapon: string;
    index: number;
    yaw: number;
    pitch: number;
    scoped?: number;
    expect: {
      offset: [number, number];
      yaw: number;
      pitch: number;
      direction: Vec;
      cone: number;
    };
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


describe('the client body matches the server body', () => {
  // The fixture is stamped by the Python side, which is the authority. This is the
  // other half of the guard `test_the_shared_fixture_is_not_stale` provides: that
  // one catches a fixture gone stale against the server, this one catches the
  // TypeScript default having drifted from the server's — a body the prediction
  // uses and the hit resolution does not, which shows up as shots that "should
  // have hit" rather than as anything that looks like a bug.
  it('has the same dimensions the server serves', () => {
    expect(DEFAULT_HITBOX).toEqual(vectors.hitbox);
  });

  it('agrees on the spec id, so a tuned body cannot masquerade as the shipped one', () => {
    expect(DEFAULT_HITBOX.specId).toBe(vectors.hitboxSpecId);
  });
});

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
      const { distance, face } = raycastWorldFace(
        world,
        testCase.origin,
        aimVector(testCase.yaw, testCase.pitch),
        testCase.max_distance,
      );
      expect(distance).toBeCloseTo(testCase.expect, -Math.log10(vectors.tolerance));
      // The face is what a bullet mark is oriented by. A port that got the sign
      // backwards draws every mark on the inside of the wall it hit — invisible,
      // and indistinguishable from decals never having been implemented.
      if (testCase.face !== null) expect(face).toBe(testCase.face);
    });
  }
});

/**
 * The recoil pattern's *application*.
 *
 * The offsets themselves are served on `GET /api/hassault/weapons` and appear in
 * this fixture verbatim, so there is one copy of the numbers by construction.
 * What can drift is what each of the four consumers does with one — the server,
 * this client's camera, this client's training range, and the native client —
 * and the mistake that matters is silent: the table is **absolute** and a camera
 * accumulates, so applying the absolute walks the crosshair away by the running
 * sum and reads as a number somebody tuned badly rather than as a bug.
 */
describe('cross-language spray conformance', () => {
  it('has spray vectors to check', () => {
    expect(vectors.sprays.length).toBeGreaterThan(0);
    expect(Object.keys(vectors.weapons).length).toBeGreaterThan(0);
  });

  for (const testCase of vectors.sprays) {
    it(testCase.name, () => {
      const weapon = vectors.weapons[testCase.weapon];
      const offset = sprayOffset(weapon, testCase.index);
      const [yaw, pitch] = applySpray(testCase.yaw, testCase.pitch, offset);
      const digits = -Math.log10(vectors.tolerance);
      expect(offset[0]).toBeCloseTo(testCase.expect.offset[0], digits);
      expect(offset[1]).toBeCloseTo(testCase.expect.offset[1], digits);
      expect(yaw).toBeCloseTo(testCase.expect.yaw, digits);
      expect(pitch).toBeCloseTo(testCase.expect.pitch, digits);
      // And the direction the server built from those angles, so a port that
      // applied the offset to the *vector* instead of to the angles is caught
      // rather than agreeing on every intermediate number and missing.
      const direction = aimVector(yaw, pitch);
      for (let i = 0; i < 3; i++) {
        expect(direction[i]).toBeCloseTo(testCase.expect.direction[i], digits);
      }
      expect(residualSpread(weapon, testCase.scoped ?? 0)).toBeCloseTo(
        testCase.expect.cone,
        digits,
      );
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
