/**
 * The hand animation tracks.
 *
 * Two properties do most of the work here and both are silent when broken. A
 * locomotion track whose ends differ ticks once per stride — visible, but easy
 * to mistake for the bob. And a keyframe naming a channel that does not exist is
 * a motion nobody ever sees, with nothing to say why.
 */
import { describe, expect, it } from 'vitest';

import {
  ACTION_CLIPS,
  AuthoredPoseSource,
  LOCOMOTION_CLIPS,
  LAND_DURATION,
  mergePose,
  sampleTrack,
  selectLocomotion,
  fadeFor,
  type PartialPose,
  type Track,
} from '../viewclips';

const CHANNELS = ['primary', 'support', 'primaryRoll', 'supportRoll'];

const source = new AuthoredPoseSource();

describe('the clip tables', () => {
  it('carries the five locomotion clips and the four actions', () => {
    expect(LOCOMOTION_CLIPS.sort()).toEqual(['idle', 'jump', 'land', 'run', 'walk']);
    expect(ACTION_CLIPS.sort()).toEqual(['draw', 'inspect', 'reload', 'throw']);
  });

  it('names no channel that is not a channel', () => {
    // A keyframe with a typo'd channel is a motion that never plays, and nothing
    // anywhere says so.
    for (const clip of LOCOMOTION_CLIPS) {
      for (const t of [0, 0.25, 0.5, 0.75, 1]) {
        for (const key of Object.keys(source.locomotion(clip, t))) {
          expect(CHANNELS).toContain(key);
        }
      }
    }
    for (const clip of ACTION_CLIPS) {
      for (const t of [0, 0.25, 0.5, 0.75, 1]) {
        for (const key of Object.keys(source.action(clip, t))) {
          expect(CHANNELS).toContain(key);
        }
      }
    }
  });

  it('loops every locomotion clip continuously', () => {
    // `t = 0` and `t = 1` must be the same pose. A track whose ends differ jumps
    // once per stride, which reads as a stutter in the walk rather than as a
    // keyframe somebody forgot to close.
    for (const clip of LOCOMOTION_CLIPS) {
      expect(source.locomotion(clip, 0)).toEqual(source.locomotion(clip, 1));
    }
  });

  it('starts and ends every action at rest', () => {
    // An action that ends somewhere other than the grip leaves the hand there
    // until the next one runs.
    for (const clip of ACTION_CLIPS) {
      // `draw` is the exception and says so: it exists to bring the hands *up*
      // into frame, so its first keyframe is deliberately away from the gun.
      if (clip !== 'draw') expect(source.action(clip, 0)).toEqual({});
      expect(source.action(clip, 1)).toEqual({});
    }
  });
});

describe('sampleTrack', () => {
  const track: Track = [
    { t: 0, pose: {} },
    { t: 0.5, pose: { support: [1, 2, 3], supportRoll: 0.4 } },
    { t: 1, pose: {} },
  ];

  it('is exact at a keyframe', () => {
    expect(sampleTrack(track, 0.5)).toEqual({ support: [1, 2, 3], supportRoll: 0.4 });
  });

  it('interpolates between them', () => {
    const mid = sampleTrack(track, 0.25);
    expect(mid.support![0]).toBeCloseTo(0.5, 9);
    expect(mid.supportRoll).toBeCloseTo(0.2, 9);
  });

  it('leaves a channel nobody mentions alone', () => {
    // The whole reason a partial keyframe is legal: `reload` is written entirely
    // in terms of the support hand, and the trigger hand has to stay on the gun.
    expect(sampleTrack(track, 0.25).primary).toBeUndefined();
  });

  it('clamps rather than extrapolating', () => {
    expect(sampleTrack(track, -5)).toEqual({});
    expect(sampleTrack(track, 5)).toEqual({});
  });

  it('survives an empty or single-frame track', () => {
    expect(sampleTrack([], 0.5)).toEqual({});
    expect(sampleTrack([{ t: 0, pose: { primaryRoll: 1 } }], 0.9)).toEqual({
      primaryRoll: 1,
    });
  });
});

describe('mergePose', () => {
  it('lets the action replace the channels it names, and only those', () => {
    // **Replace, not blend.** `models/clips.ts` already explains that averaging
    // two poses on one bone gives half a reload — a motion belonging to neither.
    const walking: PartialPose = { primary: [1, 1, 1], support: [2, 2, 2] };
    const reloading: PartialPose = { support: [9, 9, 9] };
    expect(mergePose(walking, reloading)).toEqual({
      primary: [1, 1, 1],
      support: [9, 9, 9],
    });
  });
});

describe('selectLocomotion', () => {
  const base = { moveSpeed: 20, onGround: true, sinceLanded: 99 };

  it('picks a clip by how fast you are actually going', () => {
    expect(selectLocomotion({ ...base, speed: 0 })).toBe('idle');
    expect(selectLocomotion({ ...base, speed: 8 })).toBe('walk');
    expect(selectLocomotion({ ...base, speed: 20 })).toBe('run');
  });

  it('lets airborne beat everything', () => {
    // A walk cycle in mid-air reads as a bug — the same call `viewmodel.update`
    // already makes about the bob.
    expect(selectLocomotion({ ...base, speed: 20, onGround: false })).toBe('jump');
  });

  it('plays the landing dip and then stops', () => {
    expect(selectLocomotion({ ...base, speed: 0, sinceLanded: 0 })).toBe('land');
    expect(selectLocomotion({ ...base, speed: 0, sinceLanded: LAND_DURATION + 0.01 })).toBe('idle');
  });
});

describe('fadeFor', () => {
  it('never eases into a landing', () => {
    // A landing is an impact, and its whole content is the suddenness.
    expect(fadeFor('land')).toBe(0);
  });

  it('eases into everything else', () => {
    for (const clip of ['idle', 'walk', 'run', 'jump', 'reload', 'inspect'] as const) {
      expect(fadeFor(clip)).toBeGreaterThan(0);
    }
  });
});
