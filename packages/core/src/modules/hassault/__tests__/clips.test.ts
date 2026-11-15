/**
 * Clip selection: which animation a player's state should be playing.
 *
 * Pure and three-free, so it runs headless the same way the physics vectors do.
 * The point of testing it apart from the renderer is that a wrong clip is not a
 * crash — it is an enemy who appears to be walking forward while retreating.
 */

import { describe, expect, it } from 'vitest';

import {
  IDLE_SPEED,
  OPERATOR_CLIPS,
  boneKey,
  fadeFor,
  isOneShot,
  isUpperBody,
  selectDeath,
  selectLocomotion,
  type OperatorState,
} from '../models/clips';
import manifest from '../models/clips.json';

function state(over: Partial<OperatorState> = {}): OperatorState {
  return {
    alive: true,
    ground: true,
    crouch: 0,
    speed: 0,
    ...over,
  };
}

describe('operator clip manifest', () => {
  it('matches what the build script baked into the GLB', () => {
    // The union in clips.ts is hand-maintained so the compiler can check it;
    // clips.json is generated. A clip renamed upstream must not silently become
    // a name the mixer looks up and never finds.
    const built = manifest.clips.map((c) => c.name).sort();
    expect([...OPERATOR_CLIPS].sort()).toEqual(built);
  });

  it('was built with root motion stripped', () => {
    // The server owns position. A clip that walks the hips across the floor
    // fights it, and the avatar skates away from where it actually is.
    expect(manifest.rootMotion).toBe('strip');
  });

  it('was scaled to the canonical body height', () => {
    expect(manifest.targetHeight).toBe(5.2);
  });
});

describe('selectLocomotion', () => {
  it('holds the aiming pose standing, at any speed', () => {
    // The legs are solved under it (`locomotion.ts`); the walk and strafe clips
    // are no longer chosen, because their feet do not keep up with the body.
    for (const speed of [0, IDLE_SPEED + 0.5, 4, 18]) {
      expect(selectLocomotion(state({ speed }))).toBe('rifle_aiming_idle');
    }
  });

  it('lets death and airborne override everything', () => {
    // A player killed mid-run is dying, not running.
    expect(selectLocomotion(state({ alive: false, speed: 8 }))).toBe('death_from_the_front');
    expect(selectLocomotion(state({ ground: false, speed: 8 }))).toBe('running_jump');
  });

  it('crouches, and kneels only when still', () => {
    // Crouched positions wobble a fraction of a cube at rest; without a floor
    // that wobble would flick a kneeling enemy in and out of the crouch-walk.
    expect(selectLocomotion(state({ crouch: 1, speed: 3 }))).toBe('crouch_walking');
    expect(selectLocomotion(state({ crouch: 1, speed: 0 }))).toBe('rifle_crouch_walk_to_kneel');
    expect(selectLocomotion(state({ crouch: 1, speed: IDLE_SPEED - 0.01 }))).toBe(
      'rifle_crouch_walk_to_kneel',
    );
  });

  it('only ever names a clip the GLB actually contains', () => {
    const every: OperatorState[] = [];
    for (const alive of [true, false]) {
      for (const ground of [true, false]) {
        for (const crouch of [0, 1]) {
          for (const speed of [0, 3, 12]) {
            every.push(state({ alive, ground, crouch, speed }));
          }
        }
      }
    }
    for (const s of every) {
      expect(OPERATOR_CLIPS).toContain(selectLocomotion(s));
    }
  });
});

describe('selectDeath', () => {
  it('distinguishes a headshot from behind', () => {
    expect(selectDeath(true, true)).toBe('death_from_back_headshot');
    expect(selectDeath(true, false)).toBe('death_from_the_front');
    expect(selectDeath(false, false)).toBe('dying');
  });
});

describe('bone classification', () => {
  it('strips the mixamo prefix and any glTF uniquifier', () => {
    expect(boneKey('mixamorigRightHand')).toBe('RightHand');
    expect(boneKey('mixamorig:RightHand')).toBe('RightHand');
    expect(boneKey('mixamorigHips_1')).toBe('Hips');
    expect(boneKey('mixamorigSpine2.quaternion')).toBe('Spine2');
  });

  it('splits the body so a fire action and a walk cannot fight over a bone', () => {
    // The mixer averages two actions on one bone rather than letting one win, so
    // a fire clip blended over a walk comes out as a half-shrug. Disjoint track
    // sets are what make layering work at all.
    expect(isUpperBody('mixamorigRightForeArm.quaternion')).toBe(true);
    expect(isUpperBody('mixamorigHead.quaternion')).toBe(true);
    expect(isUpperBody('mixamorigRightHandIndex3.quaternion')).toBe(true);

    expect(isUpperBody('mixamorigHips.position')).toBe(false);
    expect(isUpperBody('mixamorigLeftUpLeg.quaternion')).toBe(false);
    expect(isUpperBody('mixamorigSpine.quaternion')).toBe(false);
  });
});

describe('transitions', () => {
  it('snaps into death instead of easing it', () => {
    expect(fadeFor('dying')).toBeLessThan(0.1);
    expect(fadeFor('death_from_back_headshot')).toBeLessThan(0.1);
    expect(fadeFor('standard_walk')).toBeGreaterThan(0.1);
  });

  it('holds one-shots on their last frame rather than looping', () => {
    expect(isOneShot('dying')).toBe(true);
    expect(isOneShot('reloading')).toBe(true);
    expect(isOneShot('running_jump')).toBe(true);
    expect(isOneShot('standard_walk')).toBe(false);
    expect(isOneShot('rifle_aiming_idle')).toBe(false);
  });
});
