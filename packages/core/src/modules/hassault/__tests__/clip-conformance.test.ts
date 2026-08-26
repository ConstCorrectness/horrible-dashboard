/**
 * Clip selection, replayed against the vectors the native client also replays.
 *
 * The operator now exists in two clients: this one and the Rust client in
 * `apps/native-fps`. Both read the same GLB and both have to answer the same
 * question — given what the server says a player is doing, which of the 23 clips
 * is that? Two implementations of one decision drift, and a drifted one does not
 * throw: it just shows an enemy walking forward while they retreat, or standing
 * idle while they close on you.
 *
 * So both sides replay `clip-vectors.json`. The fixture pins **agreement**; what
 * argues the rules are right is `clips.test.ts` here and the unit tests in
 * `apps/native-fps/src/clips.rs` there. This is the same arrangement the physics
 * has in `conformance.test.ts`, for the same reason.
 */
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import {
  OPERATOR_CLIPS,
  boneKey,
  fadeFor,
  isOneShot,
  isUpperBody,
  selectDeath,
  selectLocomotion,
  type OperatorClip,
  type OperatorState,
} from '../models/clips';

/**
 * Narrow a name out of the fixture to the union the API takes.
 *
 * A bare cast would compile and then hand `fadeFor` a clip that does not exist,
 * which returns the default fade rather than failing — so a typo in the fixture
 * would pin agreement on a clip neither client has. This asserts instead.
 */
function clipName(name: string): OperatorClip {
  expect(OPERATOR_CLIPS, `${name} is not a clip in the set`).toContain(name);
  return name as OperatorClip;
}

interface Vectors {
  locomotion: { why?: string; state: OperatorState; clip: string }[];
  death: { headshot: boolean; fromBehind: boolean; clip: string }[];
  fade: { why?: string; clip: string; seconds: number }[];
  oneShot: { clip: string; once: boolean }[];
  boneKeys: { why?: string; name: string; key: string; upper: boolean }[];
}

// Read rather than imported, so the path is obviously the same file the Rust
// test names — see the note on the physics vectors.
const vectors = JSON.parse(
  readFileSync(new URL('./clip-vectors.json', import.meta.url), 'utf-8'),
) as Vectors;

describe('clip vectors', () => {
  it('covers every branch of the locomotion decision', () => {
    // A fixture that has quietly stopped covering the tree still passes every
    // case in it. The count is a floor, not an assertion about the exact set.
    expect(vectors.locomotion.length).toBeGreaterThanOrEqual(19);
    const chosen = new Set(vectors.locomotion.map((c) => c.clip));
    for (const clip of [
      'death_from_the_front',
      'running_jump',
      'crouch_walking',
      'rifle_crouch_walk_to_kneel',
      'rifle_aiming_idle',
      'right_strafe_walking',
      'strafing',
      'walking_backwards',
      'injured_run',
      'rifle_walk',
      'standard_walk',
    ]) {
      expect(chosen, `no vector selects ${clip}`).toContain(clip);
    }
  });

  for (const { why, state, clip } of vectors.locomotion) {
    it(`selects ${clip}${why ? ` — ${why}` : ''}`, () => {
      expect(selectLocomotion(state)).toBe(clip);
    });
  }

  for (const { headshot, fromBehind, clip } of vectors.death) {
    it(`death headshot=${headshot} fromBehind=${fromBehind} is ${clip}`, () => {
      expect(selectDeath(headshot, fromBehind)).toBe(clip);
    });
  }

  for (const { clip, seconds } of vectors.fade) {
    it(`fades into ${clip} over ${seconds}s`, () => {
      expect(fadeFor(clipName(clip))).toBeCloseTo(seconds, 6);
    });
  }

  for (const { clip, once } of vectors.oneShot) {
    it(`${clip} ${once ? 'plays once' : 'loops'}`, () => {
      expect(isOneShot(clipName(clip))).toBe(once);
    });
  }

  for (const { why, name, key, upper } of vectors.boneKeys) {
    it(`sanitises ${name}${why ? ` — ${why}` : ''}`, () => {
      expect(boneKey(name)).toBe(key);
      expect(isUpperBody(name)).toBe(upper);
    });
  }
});
