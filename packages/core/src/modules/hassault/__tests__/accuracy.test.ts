/**
 * `shotCone` against `accuracy-vectors.json`, which
 * `scripts/gen_hassault_accuracy_vectors.py` writes from `weapons.shot_cone`.
 *
 * The server fires every shot through that function; the training range fires
 * through this port. A range that taught a different cone from a match would
 * teach the wrong thing about stopping to shoot.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import type { WeaponSpec } from '../api';
import { MOVE_SPEED } from '../player';
import { ACCURATE_SPEED_SCALE, shotCone, type ShotContext } from '../trace';

interface Vectors {
  weaponsSpecId: string;
  moveSpeed: number;
  accurateSpeedScale: number;
  tolerance: number;
  weapons: Record<string, WeaponSpec>;
  cases: { name: string; weapon: string; ctx: ShotContext; expect: number }[];
}

const vectors: Vectors = JSON.parse(
  readFileSync(new URL('./accuracy-vectors.json', import.meta.url), 'utf-8'),
);

describe('cross-language accuracy conformance', () => {
  it('agrees on the constants the model is built from', () => {
    expect(MOVE_SPEED).toBe(vectors.moveSpeed);
    expect(ACCURATE_SPEED_SCALE).toBe(vectors.accurateSpeedScale);
    expect(vectors.cases.length).toBeGreaterThan(0);
  });

  for (const testCase of vectors.cases) {
    it(testCase.name, () => {
      const weapon = vectors.weapons[testCase.weapon];
      expect(shotCone(weapon, testCase.ctx)).toBeCloseTo(
        testCase.expect,
        -Math.log10(vectors.tolerance),
      );
    });
  }

  it('an old backend, with no accuracy fields, fires its old cone', () => {
    const rifle = { ...vectors.weapons.assault };
    for (const key of [
      'firstShotSpread',
      'moveInaccuracy',
      'airInaccuracy',
      'crouchScale',
      'perShotInaccuracy',
      'recoveryTime',
    ] as const) {
      delete rifle[key];
    }
    const running: ShotContext = {
      speed: MOVE_SPEED,
      airborne: true,
      crouch: 1,
      scoped: 0,
      sinceLastShot: 0,
    };
    expect(shotCone(rifle, running)).toBe(rifle.residualSpread);
  });
});
