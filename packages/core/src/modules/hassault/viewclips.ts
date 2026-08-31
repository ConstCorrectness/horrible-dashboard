/**
 * What the hands do, as two layers of keyframed offsets.
 *
 * ## Offsets, not positions
 *
 * Every pose is an offset from the weapon's **grip anchor** (`arms.ts`,
 * `models/grips.json`), in the weapon's own space. That is what makes a partial
 * keyframe legal: a track that says nothing about a hand leaves it on the gun,
 * so `reload` can be written entirely in terms of the support hand and never
 * mention the trigger hand it is not moving.
 *
 * ## Two layers, and why they must be channel-disjoint
 *
 * The base layer is **locomotion** — idle, walk, run, jump, land — looping, and
 * phase-driven off the view model's own `bobPhase` so it cannot drift out of
 * step with the bob. The upper layer is **actions** — reload, inspect, throw,
 * draw — one-shot.
 *
 * The action layer **replaces** the channels it names; locomotion supplies the
 * rest. It is not a weighted average, and `models/clips.ts` already explains
 * why: two poses averaged on one bone give you half a reload, which is a motion
 * that belongs to neither animation. That file makes its track sets disjoint via
 * `isUpperBody`; this one does it per channel, which is the same idea with four
 * channels instead of a skeleton.
 *
 * ## `t` is a fraction, never seconds
 *
 * The reload track stretches to whatever `reloadTime` the server served, so one
 * authored motion serves a 1.4s pistol and a 2.6s shotgun and both come back up
 * on the frame the magazine is full. Written in seconds the two would need a
 * table nobody would keep in step with `weapons.py` — the same argument
 * `RELOAD_DIP_IN` makes.
 *
 * ## The seam
 *
 * `PoseSource` is the one place a different *kind* of animation could arrive.
 * The authored tracks below are one implementation; rigged clips off a skeleton
 * would be another, and nothing in `arms.ts` or the view model would change.
 * One interface, no speculative machinery.
 */
import clips from './models/viewclips.json';

/** The four channels a pose can move. Anything not named is left on the gun. */
export interface ArmPose {
  primary: [number, number, number];
  support: [number, number, number];
  primaryRoll: number;
  supportRoll: number;
}

export type PartialPose = Partial<ArmPose>;

export interface Keyframe {
  /** Normalised over the clip's own length, 0..1. Never seconds. */
  t: number;
  pose: PartialPose;
}

export type Track = Keyframe[];

export type LocomotionClip = 'idle' | 'walk' | 'run' | 'jump' | 'land';
export type ActionClip = 'reload' | 'inspect' | 'throw' | 'draw';

interface ClipFile {
  locomotion: Record<string, Track>;
  actions: Record<string, Track>;
}

const CLIPS = clips as unknown as ClipFile;

/** How long the land dip lasts, in seconds. Short — it is a jolt, not a pose. */
export const LAND_DURATION = 0.22;
/** How long a draw takes. Matches the view model's own `DRAW_TIME`. */
export const DRAW_DURATION = 0.25;
/** The grenade throw's three phases, end to end. */
export const THROW_DURATION = 0.55;

/**
 * Which locomotion clip this frame wants.
 *
 * Airborne beats everything: a player is either on the ground moving or they are
 * not, and a walk cycle in mid-air reads as a bug — the same call
 * `viewmodel.update` already makes about the bob.
 */
export function selectLocomotion(state: {
  speed: number;
  moveSpeed: number;
  onGround: boolean;
  sinceLanded: number;
}): LocomotionClip {
  if (!state.onGround) return 'jump';
  if (state.sinceLanded < LAND_DURATION) return 'land';
  const fraction = state.moveSpeed > 0 ? state.speed / state.moveSpeed : 0;
  if (fraction < 0.15) return 'idle';
  return fraction < 0.7 ? 'walk' : 'run';
}

/** How long a crossfade into this clip should take, in seconds. */
export function fadeFor(clip: LocomotionClip | ActionClip): number {
  // A landing is an impact and must not be eased into — it is the one clip whose
  // whole content is the suddenness.
  if (clip === 'land') return 0;
  if (clip === 'jump') return 0.08;
  return 0.14;
}

function lerp3(
  a: [number, number, number],
  b: [number, number, number],
  t: number,
): [number, number, number] {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

const ZERO3: [number, number, number] = [0, 0, 0];

/**
 * Sample one track at a normalised time.
 *
 * A channel absent from **both** surrounding keyframes stays absent from the
 * result — that is the whole of "a partial track leaves that hand alone". A
 * channel present in only one is interpolated against zero, since zero is the
 * grip anchor itself.
 */
export function sampleTrack(track: Track, t: number): PartialPose {
  if (track.length === 0) return {};
  const clamped = Math.max(0, Math.min(1, t));
  if (track.length === 1 || clamped <= track[0].t) return { ...track[0].pose };
  const last = track[track.length - 1];
  if (clamped >= last.t) return { ...last.pose };

  let i = 0;
  while (i < track.length - 2 && track[i + 1].t < clamped) i += 1;
  const a = track[i];
  const b = track[i + 1];
  const span = b.t - a.t;
  const k = span <= 1e-9 ? 0 : (clamped - a.t) / span;

  const out: PartialPose = {};
  if (a.pose.primary !== undefined || b.pose.primary !== undefined) {
    out.primary = lerp3(a.pose.primary ?? ZERO3, b.pose.primary ?? ZERO3, k);
  }
  if (a.pose.support !== undefined || b.pose.support !== undefined) {
    out.support = lerp3(a.pose.support ?? ZERO3, b.pose.support ?? ZERO3, k);
  }
  if (a.pose.primaryRoll !== undefined || b.pose.primaryRoll !== undefined) {
    const from = a.pose.primaryRoll ?? 0;
    out.primaryRoll = from + ((b.pose.primaryRoll ?? 0) - from) * k;
  }
  if (a.pose.supportRoll !== undefined || b.pose.supportRoll !== undefined) {
    const from = a.pose.supportRoll ?? 0;
    out.supportRoll = from + ((b.pose.supportRoll ?? 0) - from) * k;
  }
  return out;
}

/**
 * Lay an action over a locomotion pose.
 *
 * **Replace, not blend.** A channel the action names is the action's; everything
 * else is locomotion's. See the module header for why averaging them is wrong.
 */
export function mergePose(base: PartialPose, action: PartialPose): PartialPose {
  return { ...base, ...action };
}

/** Where a pose source's poses come from. See the module header's seam note. */
export interface PoseSource {
  locomotion(clip: LocomotionClip, phase: number): PartialPose;
  action(clip: ActionClip, t: number): PartialPose;
}

/** The keyframed tracks in `models/viewclips.json`. The only source today. */
export class AuthoredPoseSource implements PoseSource {
  locomotion(clip: LocomotionClip, phase: number): PartialPose {
    const track = CLIPS.locomotion[clip];
    if (!track) return {};
    // Wrapped rather than clamped: locomotion loops, and a phase that ran past 1
    // would freeze the cycle at its last keyframe.
    return sampleTrack(track, phase - Math.floor(phase));
  }

  action(clip: ActionClip, t: number): PartialPose {
    const track = CLIPS.actions[clip];
    return track ? sampleTrack(track, t) : {};
  }
}

/** Every clip name the file actually carries. Exported for the tests. */
export const LOCOMOTION_CLIPS = Object.keys(CLIPS.locomotion).filter(
  (k) => !k.startsWith('_'),
) as LocomotionClip[];
export const ACTION_CLIPS = Object.keys(CLIPS.actions).filter(
  (k) => !k.startsWith('_'),
) as ActionClip[];
