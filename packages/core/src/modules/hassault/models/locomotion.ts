/**
 * Where an operator's feet go, worked out from how its body is moving.
 *
 * The clips could not do this. Their root motion is stripped and they play at
 * a fixed rate, so the fastest stride any of them implies is 3.4 cubes a second
 * under a body doing 18 — the feet covered a fifth of the ground the body did,
 * and everybody skated. Scaling the playback rate fixes one speed in one
 * direction; a strafe, a diagonal or a stop still slides.
 *
 * So the legs are solved instead of played. Each foot is either **planted** —
 * and then it does not move in the world, whatever the body does — or
 * **swinging** to where the body will need it next. Slide is zero by
 * construction rather than by tuning, at any speed and in any direction.
 *
 * Three-free and side-effect-free, like `clips.ts`: this is arithmetic on
 * positions, so it is tested headless. The native client carries a line-for-line
 * port in `apps/native-fps/src/locomotion.rs`; both replay
 * `__tests__/locomotion-vectors.json`, which this side writes.
 *
 * Everything is in cube space: `x`/`y` on the ground, heights up, yaw in
 * radians about the vertical with heading `(cos yaw, sin yaw)`. Foot 0 is the
 * left one, which is the *negative* side of `(-sin yaw, cos yaw)`.
 */

import { IDLE_SPEED } from './clips';

/** A position jump this fast is a respawn, not a sprint. */
export const TELEPORT_SPEED = 60;
/** How fast the measured velocity follows the raw one, per second. */
export const VELOCITY_RATE = 14;
/** The speed the gait is tuned to top out at: `MOVE_SPEED` in the physics. */
export const RUN_SPEED = 18;

/** Stride cycles a second (two steps each), as a line in speed. */
export const CADENCE_BASE = 0.9;
export const CADENCE_PER_SPEED = 0.065;
export const CADENCE_MAX = 2.2;

/** The largest share of a cycle one foot spends on the ground: a walk. */
export const DUTY_MAX = 0.6;
/** The share a slow stride is quickened to keep: just over half, so one foot
 * is always down. */
export const DUTY_GROUNDED = 0.52;
/** The smallest: below this a foot would touch down for a single frame. */
export const DUTY_MIN = 0.12;

/** How far the hips sink into a stride, in cubes. */
export const DROP_BASE = 0.06;
export const DROP_PER_SPEED = 0.008;
export const DROP_MAX = 0.25;
/** A leg is never asked to reach further than this share of its length. */
export const REACH = 0.97;
/** The share of the reachable ground a stance is planned to use. */
export const STANCE_MARGIN = 0.9;

/** How high a swinging foot clears the ground, in cubes. */
export const LIFT_BASE = 0.25;
export const LIFT_PER_SPEED = 0.03;
export const LIFT_MAX = 0.8;
/** The most a swinging foot points its toe down, then up, in radians. */
export const FOOT_PITCH = 0.5;

/** The most the pelvis turns off the look direction toward travel, radians. */
export const HIP_YAW_MAX = 0.6;
export const HIP_YAW_RATE = 10;
/** Lean into travel, in radians per cube/second. */
export const LEAN_FORWARD = 0.01;
export const LEAN_SIDE = 0.006;
/** The hips' vertical bounce at a full run, in cubes. */
export const BOB = 0.1;
/** How fast the solved legs take over from the clip's, per second. */
export const WEIGHT_RATE = 9;

export interface Foot {
  /** Where the foot is, on the ground plane. */
  x: number;
  y: number;
  /** Where it left the ground, while it swings. */
  fromX: number;
  fromY: number;
  planted: boolean;
  /** Height above the ground. */
  lift: number;
  /** Toe-down (positive) or toe-up tilt about the pelvis' side axis. */
  pitch: number;
}

/** What is remembered between frames for one body. */
export interface Gait {
  ready: boolean;
  /** Last frame's position, for the velocity. */
  px: number;
  py: number;
  vx: number;
  vy: number;
  /** Where in the stride cycle the left foot is, 0 to 1. */
  phase: number;
  /** 0 the clip's legs, 1 the solved ones. */
  weight: number;
  hipYaw: number;
  feet: [Foot, Foot];
}

export interface GaitInput {
  x: number;
  y: number;
  yaw: number;
  alive: boolean;
  grounded: boolean;
  /** The hip joints' height above a planted ankle, in the pose being played. */
  hipHeight: number;
  /** Thigh plus shin. */
  legLength: number;
  /** Distance between the two hip joints. */
  hipWidth: number;
}

export interface GaitOutput {
  /** Ground speed, cubes a second. */
  speed: number;
  /** Velocity along and across the look direction, cubes a second. */
  forward: number;
  strafe: number;
  /** 0 the clip's legs, 1 the solved ones. */
  weight: number;
  /** How far to lower the hips, in cubes (negative lifts them). */
  drop: number;
  /** Pelvis heading relative to the look direction, in the sense of yaw. */
  hipYaw: number;
  /** Lean toward the look direction and toward the strafe-positive side. */
  leanForward: number;
  leanSide: number;
  feet: [FootTarget, FootTarget];
}

export interface FootTarget {
  x: number;
  y: number;
  lift: number;
  pitch: number;
}

function foot(): Foot {
  return { x: 0, y: 0, fromX: 0, fromY: 0, planted: true, lift: 0, pitch: 0 };
}

export function createGait(): Gait {
  return {
    ready: false,
    px: 0,
    py: 0,
    vx: 0,
    vy: 0,
    phase: 0,
    weight: 0,
    hipYaw: 0,
    feet: [foot(), foot()],
  };
}

/** Stand both feet under the hips, as a body that has not started moving. */
function rest(gait: Gait, input: GaitInput, heading: number): void {
  const sideX = -Math.sin(heading);
  const sideY = Math.cos(heading);
  for (let i = 0; i < 2; i += 1) {
    const sign = i === 0 ? -1 : 1;
    const f = gait.feet[i];
    f.x = input.x + sideX * sign * input.hipWidth * 0.5;
    f.y = input.y + sideY * sign * input.hipWidth * 0.5;
    f.fromX = f.x;
    f.fromY = f.y;
    f.planted = true;
    f.lift = 0;
    f.pitch = 0;
  }
}

/** Advance one body's gait by `dt` seconds. Mutates `gait`. */
export function stepGait(gait: Gait, input: GaitInput, dt: number): GaitOutput {
  if (!gait.ready) {
    gait.ready = true;
    gait.px = input.x;
    gait.py = input.y;
    rest(gait, input, input.yaw);
  }

  // Velocity is not on the wire, so it is read off two positions. Smoothed
  // because those positions are interpolated and their difference is noisy.
  let rawX = (input.x - gait.px) / dt;
  let rawY = (input.y - gait.py) / dt;
  gait.px = input.x;
  gait.py = input.y;
  if (Math.hypot(rawX, rawY) > TELEPORT_SPEED) {
    rawX = 0;
    rawY = 0;
    gait.vx = 0;
    gait.vy = 0;
    gait.weight = 0;
    gait.hipYaw = 0;
  }
  const follow = 1 - Math.exp(-VELOCITY_RATE * dt);
  gait.vx += (rawX - gait.vx) * follow;
  gait.vy += (rawY - gait.vy) * follow;

  const speed = Math.min(Math.hypot(gait.vx, gait.vy), 25);
  const cos = Math.cos(input.yaw);
  const sin = Math.sin(input.yaw);
  const forward = gait.vx * cos + gait.vy * sin;
  const strafe = -gait.vx * sin + gait.vy * cos;

  const moving = input.alive && input.grounded && speed > IDLE_SPEED;
  const wasIdle = gait.weight === 0;
  const change = WEIGHT_RATE * dt;
  gait.weight = moving ? Math.min(1, gait.weight + change) : Math.max(0, gait.weight - change);

  let drop = Math.min(DROP_MAX, DROP_BASE + DROP_PER_SPEED * speed);
  // A pose that stands the legs dead straight leaves nothing to stride with.
  drop = Math.max(drop, input.hipHeight - 0.95 * input.legLength);
  const height = input.hipHeight - drop;
  const limit = REACH * input.legLength;
  const reach = Math.sqrt(Math.max(0, limit * limit - height * height));
  const stanceMax = 2 * reach * STANCE_MARGIN;
  // The stride. Cadence rises with speed — faster than the line where that is
  // what it takes to keep a foot down, since a slow walk with both feet in the
  // air is a hop. Past `CADENCE_MAX` the legs cannot keep up, and whatever
  // ground a planted leg cannot cover is covered in the air: `duty` falls,
  // which is a run.
  const cadence = Math.min(
    CADENCE_MAX,
    Math.max(
      CADENCE_BASE + CADENCE_PER_SPEED * speed,
      stanceMax > 1e-6 ? (speed * DUTY_GROUNDED) / stanceMax : CADENCE_MAX,
    ),
  );
  const duty = Math.max(
    DUTY_MIN,
    speed > 1e-6 ? Math.min(DUTY_MAX, (stanceMax * cadence) / speed) : DUTY_MAX,
  );
  const stanceTime = duty / cadence;

  // The pelvis turns part of the way toward a diagonal, and squares up again
  // for a pure strafe or a retreat: sin 2a is zero on all four axes.
  const span = Math.max(1e-6, forward * forward + strafe * strafe);
  const wanted = moving ? (HIP_YAW_MAX * 2 * forward * strafe) / span : 0;
  gait.hipYaw += (wanted - gait.hipYaw) * (1 - Math.exp(-HIP_YAW_RATE * dt));

  const heading = input.yaw + gait.hipYaw;
  if (!moving && gait.weight === 0) {
    // Fully handed back to the clip: forget the stride, so the next one starts
    // from under the hips rather than from wherever this one stopped.
    rest(gait, input, heading);
    gait.phase = 0;
  } else if (moving) {
    if (wasIdle) {
      // Start with the left foot half way through its stance, so neither foot
      // is asked to trail a whole stride behind on the first step.
      gait.phase = duty * 0.5;
    } else {
      gait.phase = (gait.phase + cadence * dt) % 1;
    }
  }

  const sideX = -Math.sin(heading);
  const sideY = Math.cos(heading);
  const lift = Math.min(LIFT_MAX, LIFT_BASE + LIFT_PER_SPEED * speed);
  const tilt = FOOT_PITCH * Math.min(1, speed / RUN_SPEED);

  for (let i = 0; i < 2; i += 1) {
    const sign = i === 0 ? -1 : 1;
    const f = gait.feet[i];
    if (!moving) {
      // Stopping: the foot comes down where it is and stays there while the
      // clip's legs fade back in.
      if (!f.planted) f.planted = true;
      f.lift *= Math.exp(-20 * dt);
      f.pitch *= Math.exp(-20 * dt);
      continue;
    }
    const homeX = input.x + sideX * sign * input.hipWidth * 0.5;
    const homeY = input.y + sideY * sign * input.hipWidth * 0.5;
    const local = (gait.phase + (i === 1 ? 0.5 : 0)) % 1;
    if (local < duty) {
      if (!f.planted) {
        // Touch down ahead of the hip by half the ground this stance will
        // cover, less whatever of it has already gone by.
        const ahead = stanceTime * 0.5 - local / cadence;
        f.planted = true;
        f.x = homeX + gait.vx * ahead;
        f.y = homeY + gait.vy * ahead;
      }
      f.lift = 0;
      f.pitch = 0;
      // A turn or a stop can leave a planted foot out of reach. Dragging it is
      // a slide, but a short one; leaving it would tear the leg off the hip.
      const dx = f.x - homeX;
      const dy = f.y - homeY;
      const away = Math.hypot(dx, dy);
      if (away > reach && away > 1e-9) {
        f.x = homeX + (dx * reach) / away;
        f.y = homeY + (dy * reach) / away;
      }
    } else {
      if (f.planted) {
        f.planted = false;
        f.fromX = f.x;
        f.fromY = f.y;
      }
      const s = (local - duty) / (1 - duty);
      // Aim at where the hip will be when this foot lands, plus the half
      // stance it lands ahead by — recomputed every frame, so a change of
      // direction mid-swing is followed rather than discovered on landing.
      const ahead = (1 - local) / cadence + stanceTime * 0.5;
      const ease = s * s * (3 - 2 * s);
      f.x = f.fromX + (homeX + gait.vx * ahead - f.fromX) * ease;
      f.y = f.fromY + (homeY + gait.vy * ahead - f.fromY) * ease;
      f.lift = lift * Math.sin(Math.PI * s);
      f.pitch = tilt * Math.sin(2 * Math.PI * s);
    }
  }

  // Lowest in the middle of each stance, which is where a runner's weight is.
  const bob =
    -BOB * Math.min(1, speed / RUN_SPEED) * Math.cos(4 * Math.PI * (gait.phase - duty * 0.5));

  return {
    speed,
    forward,
    strafe,
    weight: gait.weight,
    drop: (drop - bob) * gait.weight,
    hipYaw: gait.hipYaw * gait.weight,
    leanForward: LEAN_FORWARD * forward * gait.weight,
    leanSide: LEAN_SIDE * strafe * gait.weight,
    feet: [
      { x: gait.feet[0].x, y: gait.feet[0].y, lift: gait.feet[0].lift, pitch: gait.feet[0].pitch },
      { x: gait.feet[1].x, y: gait.feet[1].y, lift: gait.feet[1].lift, pitch: gait.feet[1].pitch },
    ],
  };
}
