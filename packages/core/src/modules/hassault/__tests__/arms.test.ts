/**
 * The hands.
 *
 * `solveTwoBone` is pure, so the part that can actually be wrong is checkable
 * headless — and the way it goes wrong is the reason this file exists: an
 * unclamped `acos` yields `NaN`, which yields a `NaN` matrix, which three
 * silently declines to draw. An arm that vanishes with no error anywhere.
 */
import { describe, expect, it } from 'vitest';

import {
  LOWER_LEN,
  SHOULDER_L,
  SHOULDER_R,
  UPPER_LEN,
  gripsFor,
  solveTwoBone,
  type Vec3,
} from '../arms';

function dist(a: Vec3, b: Vec3): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

function finite(v: Vec3): boolean {
  return v.every((n) => Number.isFinite(n));
}

describe('solveTwoBone', () => {
  const root: Vec3 = [0, 0, 0];
  const pole: Vec3 = [1, -1, 0];

  it('puts the elbow exactly a bone’s length from each end', () => {
    // The whole contract: an upper arm that reaches the elbow, and a forearm
    // that reaches the hand.
    const target: Vec3 = [0.8, -0.4, -0.6];
    const { elbow, stretched } = solveTwoBone(root, target, UPPER_LEN, LOWER_LEN, pole);
    expect(stretched).toBe(false);
    expect(dist(root, elbow)).toBeCloseTo(UPPER_LEN, 6);
    expect(dist(elbow, target)).toBeCloseTo(LOWER_LEN, 6);
  });

  it('straightens rather than NaN-ing when the target is out of reach', () => {
    // Unclamped this is `acos` of something greater than 1 — `NaN` all the way
    // down to a mesh that is silently not drawn.
    const far: Vec3 = [50, 0, 0];
    const { elbow, stretched } = solveTwoBone(root, far, UPPER_LEN, LOWER_LEN, pole);
    expect(stretched).toBe(true);
    expect(finite(elbow)).toBe(true);
    expect(dist(root, elbow)).toBeCloseTo(UPPER_LEN, 6);
  });

  it('folds rather than NaN-ing when the target is closer than the arm can fold', () => {
    // The other bound, where the offset from the shoulder-to-hand line has an
    // imaginary length and `Math.sqrt` of a negative is `NaN`.
    const near: Vec3 = [0.001, 0, 0];
    const { elbow } = solveTwoBone(root, near, UPPER_LEN, LOWER_LEN, pole);
    expect(finite(elbow)).toBe(true);
  });

  it('survives a target exactly at the shoulder', () => {
    const { elbow } = solveTwoBone(root, [0, 0, 0], UPPER_LEN, LOWER_LEN, pole);
    expect(finite(elbow)).toBe(true);
  });

  it('survives a pole parallel to the arm', () => {
    // Gram-Schmidt against a parallel vector leaves nothing, so there has to be
    // a fallback perpendicular rather than a zero-length side vector.
    const target: Vec3 = [1, 0, 0];
    const { elbow } = solveTwoBone(root, target, UPPER_LEN, LOWER_LEN, [1, 0, 0]);
    expect(finite(elbow)).toBe(true);
    expect(dist(root, elbow)).toBeCloseTo(UPPER_LEN, 6);
  });

  it('bends the elbow to the side the pole points', () => {
    // A real arm bends outward and down, not through the chest — which is the
    // only thing the pole is for.
    const target: Vec3 = [0, 0, -1];
    const out = solveTwoBone(root, target, UPPER_LEN, LOWER_LEN, [1, 0, 0]);
    const across = solveTwoBone(root, target, UPPER_LEN, LOWER_LEN, [-1, 0, 0]);
    expect(out.elbow[0]).toBeGreaterThan(0);
    expect(across.elbow[0]).toBeLessThan(0);
  });

  it('never produces a NaN over a sweep of the whole reachable space', () => {
    // Belt and braces, because the failure is invisible: the mesh is simply not
    // there, and nothing logs.
    for (let d = 0; d <= (UPPER_LEN + LOWER_LEN) * 1.5; d += 0.05) {
      for (const dir of [
        [1, 0, 0],
        [0, -1, 0],
        [0, 0, -1],
        [0.577, -0.577, -0.577],
      ] as Vec3[]) {
        const target: Vec3 = [dir[0] * d, dir[1] * d, dir[2] * d];
        expect(finite(solveTwoBone(root, target, UPPER_LEN, LOWER_LEN, pole).elbow)).toBe(true);
      }
    }
  });
});

describe('the shoulders', () => {
  it('sit either side of the eye, below it', () => {
    // Cube units: the eye is 4.5 cubes up and eyes are about 1.6m off the
    // ground, so these are shoulders about 25cm below the eye and 20cm apart.
    expect(SHOULDER_R[0]).toBeGreaterThan(0);
    expect(SHOULDER_L[0]).toBeLessThan(0);
    expect(SHOULDER_R[1]).toBeLessThan(0);
    expect(SHOULDER_L[1]).toBe(SHOULDER_R[1]);
  });

  it('are within reach of where a weapon is held', () => {
    // `HOME` is (0.92, -0.86, -1.35) and the grips sit near it. If an arm cannot
    // reach the gun it is holding, every frame is the `stretched` case and the
    // elbows lock — which looks like a rig that was never posed.
    const grips = gripsFor('assault');
    const hand: Vec3 = [
      0.92 + grips.primary[0],
      -0.86 + grips.primary[1],
      -1.35 + grips.primary[2],
    ];
    expect(dist(SHOULDER_R, hand)).toBeLessThan(UPPER_LEN + LOWER_LEN);
  });
});

describe('gripsFor', () => {
  it('gives every shipped weapon a trigger hand', () => {
    for (const id of ['knife', 'pistol', 'assault', 'shotgun', 'sniper']) {
      const grips = gripsFor(id);
      expect(grips.primary).toHaveLength(3);
      expect(grips.primary.every((n) => Number.isFinite(n))).toBe(true);
    }
  });

  it('gives the knife one hand and the rifles two', () => {
    // `null` is a real state and not a missing value: a hand parked at some
    // arbitrary coordinate is a hand the player will eventually see.
    expect(gripsFor('knife').support).toBeNull();
    expect(gripsFor('assault').support).not.toBeNull();
  });

  it('gives a weapon it has never heard of a plausible pair of hands', () => {
    // The `fitWeaponModel` spirit: measure the general case, list only the
    // exceptions. A weapon added on the server should look ordinary, not
    // empty-handed until somebody notices.
    const grips = gripsFor('railgun');
    expect(grips.support).not.toBeNull();
    expect(grips.primary.every((n) => Number.isFinite(n))).toBe(true);
  });

  it('puts the support hand forward of the trigger hand', () => {
    // -z is forward in the model's own space, so a support hand *behind* the
    // trigger hand is a rifle held backwards.
    for (const id of ['assault', 'shotgun', 'sniper']) {
      const grips = gripsFor(id);
      expect(grips.support![2]).toBeLessThan(grips.primary[2]);
    }
  });
});
