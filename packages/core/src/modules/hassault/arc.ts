/**
 * Where a grenade would land, drawn before you throw it.
 *
 * ## Why this exists at all
 *
 * The server has *always* added the thrower's own velocity to a throw
 * (`grenades.THROW_INHERIT`, 0.6 of it) — running forward sends a grenade
 * further, jumping sends it higher. Nothing on screen said so, so the feature
 * was real and invisible: the only way to learn it was to notice that grenades
 * sometimes went further than expected and guess why.
 *
 * The arc is that made legible. It is drawn from the player's **locally
 * predicted** velocity rather than from the last snapshot's, or it would lag
 * their own movement by half a round trip and the thing it exists to show would
 * be the thing it showed worst.
 *
 * ## Only to first contact, deliberately
 *
 * `step_grenade` bounces, and bouncing is chaotic: a difference of 1e-6 in the
 * floor comparison flips a bounce and puts the marker in the next room. A
 * *learnable* feature that is confidently wrong is worse than one that stops
 * early — so the preview ends where the grenade first touches something, which
 * is both stable and what a player actually aims with. Full bounce prediction is
 * on the roadmap, not in this file.
 *
 * ## Everything here is served
 *
 * `GRAVITY`, `THROW_SPEED`, `LOB_SCALE`, `THROW_INHERIT`, `THROW_FORWARD`,
 * `THROW_DROP` and the integrator's own step come from
 * `GET /api/hassault/throw`. Retyping them here would be a preview that quietly
 * disagreed with the throw it is previewing, which is the one thing an aiming
 * aid must not do. A missing `ThrowPhysics` draws nothing at all rather than
 * integrating with zeros.
 *
 * Deliberately free of three and of React, so all of it is unit-testable
 * headless — `combat.ts` and `utility.ts`'s rule.
 */
import type { ThrowPhysics } from './api';
import type { World } from './world';
import type { Vec } from './trace';

/** How far ahead the preview looks, in seconds. */
export const ARC_PREVIEW_SECONDS = 2.0;

/** How many points the drawn line has. Enough to read as a curve at 2 seconds. */
export const ARC_SAMPLES = 48;

export interface ThrowArc {
  /** The flight path, in cube coordinates, ending at first contact. */
  points: Vec[];
  /**
   * Where it first touched something, or `null` if it was still in the air when
   * the preview ran out.
   *
   * `null` is a real answer and not a failure: a grenade thrown across a canyon
   * is genuinely still falling two seconds later, and drawing a marker at the
   * end of the preview window would claim it landed there.
   */
  contact: Vec | null;
  /** Whether that contact was the ground rather than a wall or a ceiling. */
  landed: boolean;
}

/**
 * Whether a point is inside the level's geometry.
 *
 * The same three questions `grenades._blocked` asks of a cell, which is
 * deliberate: a preview that stopped on different surfaces than the grenade does
 * would be an aiming aid pointing at somewhere the grenade will not be.
 */
function blocked(world: World, x: number, y: number, z: number): boolean {
  const cx = Math.floor(x);
  const cy = Math.floor(y);
  if (world.isSolid(cx, cy)) return true;
  return z < world.floorAt(cx, cy) || z > world.ceilAt(cx, cy);
}

/**
 * Where a grenade appears when it leaves the hand.
 *
 * `grenades.throw_origin`. In front of and below the eye rather than at it: a
 * grenade released exactly at the eye clips the thrower's own body on the first
 * substep when they are backed against a wall.
 */
export function throwOrigin(
  x: number,
  y: number,
  eyeZ: number,
  yaw: number,
  pitch: number,
  physics: ThrowPhysics,
): Vec {
  const cp = Math.cos(pitch);
  return [
    x + Math.cos(yaw) * cp * physics.throwForward,
    y + Math.sin(yaw) * cp * physics.throwForward,
    eyeZ - physics.throwDrop + Math.sin(pitch) * physics.throwForward,
  ];
}

/**
 * The velocity a grenade leaves the hand with.
 *
 * `grenades.throw_velocity`. The thrower's own velocity is added at
 * `throwInherit` rather than in full: at 1.0 a player running backwards can drop
 * a grenade that never leaves them, which reads as the throw having failed.
 */
export function throwVelocity(
  yaw: number,
  pitch: number,
  lob: boolean,
  inherit: Vec,
  physics: ThrowPhysics,
): Vec {
  const speed = physics.throwSpeed * (lob ? physics.lobScale : 1);
  const cp = Math.cos(pitch);
  const dx = Math.cos(yaw) * cp;
  const dy = Math.sin(yaw) * cp;
  const dz = Math.sin(pitch);
  return [
    dx * speed + inherit[0] * physics.throwInherit,
    dy * speed + inherit[1] * physics.throwInherit,
    dz * speed + inherit[2] * physics.throwInherit,
  ];
}

/**
 * Integrate a throw forward until it touches something.
 *
 * Substepped and **axis-separated** like `step_grenade`, and for its reason:
 * resolving a diagonal contact as one event has to pick an axis anyway, and
 * picking the wrong one reports a contact on a surface the grenade would have
 * slid along. Here it decides only where the preview stops, but it has to stop
 * where the real one would bounce.
 */
export function simulateThrow(
  world: World,
  origin: Vec,
  velocity: Vec,
  physics: ThrowPhysics,
  seconds = ARC_PREVIEW_SECONDS,
): ThrowArc {
  const points: Vec[] = [[origin[0], origin[1], origin[2]]];
  let [x, y, z] = origin;
  // Only the vertical velocity changes: this preview stops at first contact, so
  // there is no bounce to reflect the horizontal ones.
  const [vx, vy] = velocity;
  let vz = velocity[2];
  // One sample every so many substeps, so the drawn line has `ARC_SAMPLES`
  // points however long the preview window is.
  const substeps = Math.max(1, Math.ceil(seconds / physics.substep));
  const every = Math.max(1, Math.floor(substeps / ARC_SAMPLES));

  for (let step = 0; step < substeps; step++) {
    const h = physics.substep;
    vz -= physics.gravity * h;

    let contact: Vec | null = null;
    let landed = false;
    // x, then y, then z — the order `step_grenade` resolves them in.
    const nx = x + vx * h;
    if (blocked(world, nx, y, z)) {
      contact = [x, y, z];
    } else {
      x = nx;
    }
    if (contact === null) {
      const ny = y + vy * h;
      if (blocked(world, x, ny, z)) {
        contact = [x, y, z];
      } else {
        y = ny;
      }
    }
    if (contact === null) {
      const nz = z + vz * h;
      if (blocked(world, x, y, nz)) {
        contact = [x, y, z];
        // Falling when it stopped: the thing it met was the ground. Only then is
        // a landing marker honest — a grenade that clipped a wall is going to
        // carry on somewhere this preview does not follow.
        landed = vz < 0;
      } else {
        z = nz;
      }
    }

    if (contact !== null) {
      points.push(contact);
      return { points, contact, landed };
    }
    if (step % every === 0) points.push([x, y, z]);
  }

  points.push([x, y, z]);
  // Still in the air. Not a failure — see `ThrowArc.contact`.
  return { points, contact: null, landed: false };
}
