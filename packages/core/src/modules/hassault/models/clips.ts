/**
 * Which animation an operator should be playing, given what the server says.
 *
 * Deliberately three-free and side-effect-free, the same way `world.ts` and
 * `player.ts` are: picking a clip is a decision about game state, so it is unit
 * tested headless rather than eyeballed in a running match.
 *
 * The clip names are the ones baked into `hassault-operator.glb` by
 * `scripts/build_hassault_character.mjs`, which derives them from the Mixamo
 * filenames. `clips.json` beside this file is that script's manifest; the union
 * below is the hand-maintained mirror the compiler can actually check, and
 * `__tests__/clips.test.ts` asserts the two agree.
 */

export const OPERATOR_CLIPS = [
  'crouch_to_standing_with_rifle',
  'crouch_walking',
  'crouched_to_standing',
  'death_from_back_headshot',
  'death_from_the_front',
  'dying',
  'firing_rifle',
  'injured_run',
  'reloading',
  'rifle_aiming_idle',
  'rifle_crouch_walk_to_kneel',
  'rifle_side_step',
  'rifle_turn',
  'rifle_walk',
  'right_strafe_walking',
  'running_jump',
  'running_up_stairs',
  'standard_walk',
  'strafing',
  'turn_90_left',
  'walk_crouching_forward',
  'walk_forward_right',
  'walking_backwards',
] as const;

export type OperatorClip = (typeof OPERATOR_CLIPS)[number];

/**
 * The bones an upper-body action is allowed to drive.
 *
 * Firing and reloading have to layer over whatever the legs are doing, because
 * standing still to reload is not how anyone plays. `AnimationMixer` blends
 * overlapping actions by weighted average rather than letting one win, so two
 * actions writing the same bone produce a half-reload rather than a reload. The
 * layering therefore works by making the track sets *disjoint* — the action clip
 * is filtered to these bones and the locomotion clip is filtered to everything
 * else — and then neither has an opinion about the other's bones.
 *
 * Matched on the suffix after Mixamo's `mixamorig:` prefix, which glTF export
 * sanitises to `mixamorig` (and may suffix with `_1` where names collide).
 */
const UPPER_BODY_BONES = [
  'Spine1',
  'Spine2',
  'Neck',
  'Head',
  'HeadTop_End',
  'LeftShoulder',
  'LeftArm',
  'LeftForeArm',
  'LeftHand',
  'RightShoulder',
  'RightArm',
  'RightForeArm',
  'RightHand',
];

/** Strip the `mixamorig` prefix and any `_N` uniquifier glTF export added. */
export function boneKey(trackOrBoneName: string): string {
  const node = trackOrBoneName.split('.')[0];
  return node.replace(/^mixamorig[:_]?/, '').replace(/_\d+$/, '');
}

/** Whether a track or bone belongs to the upper body an action clip may drive. */
export function isUpperBody(trackOrBoneName: string): boolean {
  const key = boneKey(trackOrBoneName);
  return (
    UPPER_BODY_BONES.includes(key) || key.startsWith('LeftHand') || key.startsWith('RightHand')
  );
}

/** What the renderer knows about a player this frame. */
export interface OperatorState {
  alive: boolean;
  /** Standing on something. */
  ground: boolean;
  /** 0 standing, 1 fully crouched. */
  crouch: number;
  /** Ground speed in cubes/second. */
  speed: number;
  /** Velocity along the way the player is facing, -1 (backwards) to 1. */
  forward: number;
  /** Velocity across the way the player is facing, -1 (left) to 1. */
  strafe: number;
  /** Wounded enough that the limp reads as information, not decoration. */
  hurt: boolean;
}

/**
 * Below this, a player is standing still rather than walking slowly.
 *
 * Interpolated remote positions jitter by a fraction of a cube even at rest, and
 * without a floor that jitter drives the walk cycle — an idle enemy shuffling on
 * the spot, which reads as movement and is worse than no animation.
 */
export const IDLE_SPEED = 0.6;

/** Above this, the run cycle rather than the walk cycle. */
export const RUN_SPEED = 9;

/**
 * The locomotion clip for a state — the full-body base layer.
 *
 * Death is checked first and jump second because both override direction
 * entirely: a player killed mid-strafe is dying, not strafing.
 */
export function selectLocomotion(state: OperatorState): OperatorClip {
  if (!state.alive) return 'death_from_the_front';
  if (!state.ground) return 'running_jump';

  const crouched = state.crouch > 0.5;
  const moving = state.speed > IDLE_SPEED;

  if (crouched) {
    // There is no crouched idle in the set, so the crouch-walk cycle stands in;
    // held at weight it still reads as a braced low posture rather than a walk.
    return moving ? 'crouch_walking' : 'rifle_crouch_walk_to_kneel';
  }

  if (!moving) return 'rifle_aiming_idle';

  // Direction is decided by whichever axis dominates, not by blending: two
  // walk cycles at half weight each land both feet in the wrong place, whereas
  // one cycle played whole keeps its foot contacts.
  if (Math.abs(state.strafe) > Math.abs(state.forward)) {
    return state.strafe > 0 ? 'right_strafe_walking' : 'strafing';
  }
  if (state.forward < 0) return 'walking_backwards';
  if (state.hurt && state.speed > RUN_SPEED) return 'injured_run';
  return state.speed > RUN_SPEED ? 'rifle_walk' : 'standard_walk';
}

/** The death animation for how a player was killed. */
export function selectDeath(headshot: boolean, fromBehind: boolean): OperatorClip {
  if (headshot && fromBehind) return 'death_from_back_headshot';
  return headshot ? 'death_from_the_front' : 'dying';
}

/**
 * How long to crossfade into a clip.
 *
 * Death does not fade — a body snapping from a walk into a slump is the point,
 * and easing it makes the kill feel unacknowledged. Everything else fades fast
 * enough not to feel like a transition and slow enough not to pop.
 */
export function fadeFor(clip: OperatorClip): number {
  if (clip === 'dying' || clip.startsWith('death_')) return 0.05;
  if (clip === 'running_jump') return 0.08;
  return 0.18;
}

/** Clips that play once and hold their last frame rather than looping. */
export function isOneShot(clip: OperatorClip): boolean {
  return (
    clip.startsWith('death_') ||
    clip === 'dying' ||
    clip === 'reloading' ||
    clip === 'firing_rifle' ||
    clip === 'running_jump'
  );
}
