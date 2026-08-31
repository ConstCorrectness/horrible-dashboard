/**
 * The predicted throw.
 *
 * Two things are tested here and they are different. The **conformance** block
 * replays `physics-vectors.json` and pins that this integration agrees with the
 * server's — a preview that stopped somewhere the grenade will not is an aiming
 * aid pointing at the wrong place. The rest pins the properties the feature
 * exists for, most of all that running and jumping actually change where the
 * grenade goes: the server has done that since grenades existed
 * (`THROW_INHERIT`) and nothing on screen ever said so.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { ThrowPhysics } from '../api';
import { simulateThrow, throwOrigin, throwVelocity, type ThrowArc } from '../arc';
import { buildWorld, type WorldSpec } from './build-world';

interface ThrowCase {
  name: string;
  world: string;
  x: number;
  y: number;
  eyeZ: number;
  yaw: number;
  pitch: number;
  lob: boolean;
  inherit: [number, number, number];
  expect: {
    origin: [number, number, number];
    velocity: [number, number, number];
    points: [number, number, number][];
    contact: [number, number, number] | null;
    landed: boolean;
  };
}

const vectors = JSON.parse(readFileSync(resolve(__dirname, 'physics-vectors.json'), 'utf8')) as {
  worlds: Record<string, WorldSpec>;
  throws: ThrowCase[];
  throwTolerance: number;
  throwPreviewSeconds: number;
  throwArcSamples: number;
};

/**
 * The served constants, out of the backend's own module.
 *
 * There is no `weapons` block for these — they are one route with nine numbers —
 * so they are read from `grenades.py` rather than retyped. Retyping them would
 * be the second copy this whole design refuses.
 */
const PHYSICS: ThrowPhysics = (() => {
  const source = readFileSync(
    resolve(__dirname, '../../../../../../backend/modules/hassault/grenades.py'),
    'utf8',
  );
  const physics = readFileSync(
    resolve(__dirname, '../../../../../../backend/modules/hassault/physics.py'),
    'utf8',
  );
  const num = (text: string, name: string): number => {
    const m = new RegExp(`^${name}\\s*(?::[^=]+)?=\\s*([-\\d.]+)`, 'm').exec(text);
    if (!m) throw new Error(`could not read ${name}`);
    return Number(m[1]);
  };
  return {
    gravity: num(physics, 'GRAVITY'),
    throwSpeed: num(source, 'THROW_SPEED'),
    lobScale: num(source, 'LOB_SCALE'),
    throwInherit: num(source, 'THROW_INHERIT'),
    throwForward: num(source, 'THROW_FORWARD'),
    throwDrop: num(source, 'THROW_DROP'),
    restSpeed: num(source, 'REST_SPEED'),
    // `SUBSTEP = 1.0 / 120.0` is an expression, not a literal.
    substep: 1 / 120,
    maxSubsteps: num(source, 'MAX_SUBSTEPS'),
  };
})();

function run(testCase: ThrowCase): ThrowArc {
  const world = buildWorld(vectors.worlds[testCase.world]);
  return simulateThrow(
    world,
    throwOrigin(testCase.x, testCase.y, testCase.eyeZ, testCase.yaw, testCase.pitch, PHYSICS),
    throwVelocity(testCase.yaw, testCase.pitch, testCase.lob, testCase.inherit, PHYSICS),
    PHYSICS,
    vectors.throwPreviewSeconds,
  );
}

describe('the served constants', () => {
  it('are the backend’s own, not a second copy', () => {
    // If this drifts, every arc below is being drawn with numbers the server
    // does not use — which is the one failure an aiming aid must not have.
    expect(PHYSICS.gravity).toBeGreaterThan(0);
    expect(PHYSICS.throwSpeed).toBeGreaterThan(0);
    expect(PHYSICS.lobScale).toBeGreaterThan(0);
    expect(PHYSICS.lobScale).toBeLessThan(1);
    expect(PHYSICS.throwInherit).toBeGreaterThan(0);
    expect(PHYSICS.throwInherit).toBeLessThan(1);
  });
});

describe('cross-language throw conformance', () => {
  it('has throw vectors to check', () => {
    expect(vectors.throws.length).toBeGreaterThan(0);
  });

  for (const testCase of vectors.throws) {
    it(testCase.name, () => {
      const arc = run(testCase);
      const tol = vectors.throwTolerance;

      const origin = throwOrigin(
        testCase.x,
        testCase.y,
        testCase.eyeZ,
        testCase.yaw,
        testCase.pitch,
        PHYSICS,
      );
      const velocity = throwVelocity(
        testCase.yaw,
        testCase.pitch,
        testCase.lob,
        testCase.inherit,
        PHYSICS,
      );
      for (let i = 0; i < 3; i++) {
        expect(origin[i]).toBeCloseTo(testCase.expect.origin[i], -Math.log10(tol));
        expect(velocity[i]).toBeCloseTo(testCase.expect.velocity[i], -Math.log10(tol));
      }

      expect(arc.landed).toBe(testCase.expect.landed);
      if (testCase.expect.contact === null) {
        expect(arc.contact).toBeNull();
      } else {
        expect(arc.contact).not.toBeNull();
        for (let i = 0; i < 3; i++) {
          // A looser tolerance than the rest of the fixture, and it is stated in
          // the file: 1e-9 is right for one movement step and wrong for an
          // integrator run for two seconds, where the ports' float widths
          // diverge steadily. Pinning it tighter makes this flaky, and a flaky
          // test gets deleted.
          expect(arc.contact![i]).toBeCloseTo(testCase.expect.contact[i], -Math.log10(tol));
        }
      }
    });
  }
});

describe('what the arc is for', () => {
  const byName = (name: string): ThrowCase => {
    const found = vectors.throws.find((c) => c.name.startsWith(name));
    if (!found) throw new Error(`the fixture lost "${name}"`);
    return found;
  };

  it('sends a grenade further when you run at the throw', () => {
    // `THROW_INHERIT`. The server has always done this and nothing on screen
    // said so — which is the whole reason the preview exists.
    const still = run(byName('a flat throw from a standstill'));
    const running = run(byName('the same throw while running'));
    expect(still.contact).not.toBeNull();
    expect(running.contact).not.toBeNull();
    expect(running.contact![0]).toBeGreaterThan(still.contact![0]);
  });

  it('sends it further still when you jump', () => {
    // Upward momentum keeps it in the air longer, so it travels further before
    // gravity brings it down. The vertical term is part of `inherit` too.
    const still = run(byName('a flat throw from a standstill'));
    const jumping = run(byName('the same throw while jumping'));
    expect(jumping.contact![0]).toBeGreaterThan(still.contact![0]);
  });

  it('makes an underhand lob land much shorter', () => {
    // The short throw is what puts a smoke at your own feet, and it is now the
    // right mouse button rather than a second key.
    const full = run(byName('a flat throw from a standstill'));
    const lob = run(byName('an underhand lob'));
    expect(lob.contact![0]).toBeLessThan(full.contact![0]);
  });

  it('marks a landing only when the grenade actually met the ground', () => {
    // A grenade that clipped a wall carries on somewhere this preview does not
    // follow, so a ring on the floor would be claiming something false.
    for (const testCase of vectors.throws) {
      const arc = run(testCase);
      if (arc.landed) expect(arc.contact).not.toBeNull();
    }
  });

  it('draws a readable number of points and no more', () => {
    // The geometry is allocated once at `ARC_SAMPLES + 2`; more points than that
    // would be silently truncated by the renderer.
    for (const testCase of vectors.throws) {
      expect(run(testCase).points.length).toBeLessThanOrEqual(vectors.throwArcSamples + 2);
      expect(run(testCase).points.length).toBeGreaterThanOrEqual(2);
    }
  });

  it('starts in front of the eye rather than at it', () => {
    // A grenade released exactly at the eye clips the thrower's own body on the
    // first substep when they are backed against a wall.
    const origin = throwOrigin(10, 10, 4.5, 0, 0, PHYSICS);
    expect(origin[0]).toBeCloseTo(10 + PHYSICS.throwForward, 9);
    expect(origin[2]).toBeCloseTo(4.5 - PHYSICS.throwDrop, 9);
  });
});
