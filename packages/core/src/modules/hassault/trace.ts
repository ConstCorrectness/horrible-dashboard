/**
 * Shot geometry on the client: where a ray stops, and whether it went through a
 * body on the way.
 *
 * **This is not hit registration.** In a match the server owns that entirely
 * (`backend/modules/hassault/weapons.py`) and nothing here is consulted. This
 * exists for the training range, which has no server to ask — and for a shot to
 * teach you anything there, it has to stop where a real one would.
 *
 * That makes this a *third* copy of geometry the backend already has, after
 * `physics.py`/`world.ts`. The repo's answer to that is not to trust care: it is
 * `__tests__/trace-vectors.json`, generated from the Python and replayed by both
 * suites, so the two agree by test rather than by inspection. The fixture pins
 * *agreement*; each side's own tests pin correctness.
 *
 * Free of three and of React, like the rest of the game's logic files.
 */
import type { WeaponSpec } from './api';
import { currentHitbox } from './hitbox';
import { PLAYER_ABOVE_EYE, PLAYER_EYE_HEIGHT, type World } from './world';

/** Total body height — what the collision code reserves and the avatar is drawn to. */
export const BODY_HEIGHT = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE;

/**
 * The top band of the body that counts as a head.
 *
 * A band rather than an absolute height, because crouching shortens the body and
 * a head pinned to a standing figure would sit above a crouched player entirely.
 */
export const HEAD_BAND = 1.0;

export type Vec = [number, number, number];

/** A unit direction from view angles, in cube coordinates. Positive pitch is up. */
export function aimVector(yaw: number, pitch: number): Vec {
  const cp = Math.cos(pitch);
  return [cp * Math.cos(yaw), cp * Math.sin(yaw), Math.sin(pitch)];
}

/** Where a shot leaves from: the eye, which crouching lowers. */
export function eyePosition(x: number, y: number, z: number, eye = PLAYER_EYE_HEIGHT): Vec {
  return [x, y, z + eye];
}

/**
 * Which face of the world a shot stopped against, as an index.
 *
 * **The outward normal of the surface hit — the direction pointing back at the
 * shooter.** A ray stepping `+x` that enters a solid has hit that block's `-x`
 * face, so it reports `FACE_NX`. These mirror `weapons.py`'s constants exactly
 * (the server puts the index on the wire per pellet) and are re-derived here
 * only because the training range has no server to ask.
 */
export const FACE_PX = 0;
export const FACE_NX = 1;
export const FACE_PY = 2;
export const FACE_NY = 3;
export const FACE_PZ = 4;
export const FACE_NZ = 5;
/** No surface: a body, or a shot that reached its range. Negative rather than a
 * sixth value, so a caller that forgets to check cannot index a table with it. */
export const FACE_NONE = -1;

/**
 * Distance along `direction` to the first surface, or `maxDistance`.
 *
 * A thin wrapper over `raycastWorldFace`. Kept because most callers want the
 * distance and nothing else, exactly as `raycast_world` is kept on the server.
 */
export function raycastWorld(
  world: World,
  origin: Vec,
  direction: Vec,
  maxDistance: number,
): number {
  return raycastWorldFace(world, origin, direction, maxDistance).distance;
}

/**
 * Distance along `direction` to the first surface, and **which face**.
 *
 * A grid DDA, because the world *is* a grid: the ray is walked cell by cell, and
 * within each cell only two things stop it — the cell being solid, or the ray
 * leaving the gap between that cell's floor and ceiling.
 *
 * The height test uses the cell's flat floor/ceiling, so a heightfield slope is
 * treated as a step. Shots graze slopes they might have clipped by a few
 * hundredths of a cube; the alternative is per-triangle intersection against a
 * mesh this does not have.
 *
 * In a match the face comes off the wire and this is never consulted for it —
 * the server owns where a shot stopped. This exists for the training range,
 * which has no server, and its agreement with the server's copy is pinned by
 * `physics-vectors.json`.
 */
export function raycastWorldFace(
  world: World,
  origin: Vec,
  direction: Vec,
  maxDistance: number,
): { distance: number; face: number } {
  const [ox, oy, oz] = origin;
  const [dx, dy, dz] = direction;
  let cx = Math.floor(ox);
  let cy = Math.floor(oy);

  // The muzzle is inside geometry. There is no surface and no direction the ray
  // arrived from, so there is nothing to orient a mark against.
  if (world.isSolid(cx, cy)) return { distance: 0, face: FACE_NONE };

  const stepX = dx > 0 ? 1 : -1;
  const stepY = dy > 0 ? 1 : -1;
  const tDeltaX = dx !== 0 ? Math.abs(1 / dx) : Infinity;
  const tDeltaY = dy !== 0 ? Math.abs(1 / dy) : Infinity;
  let tMaxX = dx !== 0 ? (dx > 0 ? (cx + 1 - ox) / dx : (cx - ox) / dx) : Infinity;
  let tMaxY = dy !== 0 ? (dy > 0 ? (cy + 1 - oy) / dy : (cy - oy) / dy) : Infinity;

  let t = 0;
  // Which face the ray came through to reach the cell being examined. There is
  // none for the cell the muzzle is in, which is why it starts at `FACE_NONE`
  // rather than at an axis.
  let entered = FACE_NONE;
  // Bounded rather than `while (true)`: a direction of (0, 0, ±1) never leaves
  // its cell, and a loop that only exits on a boundary crossing would never end.
  const limit = 4 * world.ssize + 8;
  for (let i = 0; i < limit; i++) {
    const tExit = Math.min(tMaxX, tMaxY, maxDistance);
    const floor = world.floorAt(cx, cy);
    const ceil = world.ceilAt(cx, cy);
    // The ray is linear in z, so the crossing solves directly.
    if (dz < 0) {
      const tHit = (floor - oz) / dz;
      // Descending onto a floor: the surface faces up.
      if (t <= tHit && tHit <= tExit) return { distance: tHit, face: FACE_PZ };
    } else if (dz > 0) {
      const tHit = (ceil - oz) / dz;
      if (t <= tHit && tHit <= tExit) return { distance: tHit, face: FACE_NZ };
    } else if (oz < floor || oz > ceil) {
      // Dead level, and this cell's gap does not contain the ray — a step, or a
      // low ceiling. There is no floor or ceiling *crossing* to name, but the ray
      // did stop against the side of that step, through the face it came in by.
      return { distance: t, face: entered };
    }
    if (tExit >= maxDistance) return { distance: maxDistance, face: FACE_NONE };
    if (tMaxX < tMaxY) {
      cx += stepX;
      t = tMaxX;
      tMaxX += tDeltaX;
      entered = stepX > 0 ? FACE_NX : FACE_PX;
    } else {
      cy += stepY;
      t = tMaxY;
      tMaxY += tDeltaY;
      entered = stepY > 0 ? FACE_NY : FACE_PY;
    }
    // Stepping in +x means arriving at the block's -x face — the one pointing
    // back at the shooter.
    if (world.isSolid(cx, cy)) return { distance: t, face: entered };
  }
  return { distance: maxDistance, face: FACE_NONE };
}

/**
 * Distance at which the ray enters a body's cylinder, or `null`.
 *
 * Solved as the intersection of two intervals — inside the infinite cylinder,
 * inside the height slab — so a shot straight up or down is not a special case.
 */
export function rayHitsBody(
  origin: Vec,
  direction: Vec,
  feet: Vec,
  radiusArg?: number,
  heightArg?: number,
): number | null {
  // Optional rather than defaulted to the module constants: those bind at load, so
  // a tuned body would reach the renderer and the prediction but not the code that
  // decides whether a training shot hit — the one place it would be invisible.
  const spec = currentHitbox();
  const radius = radiusArg ?? spec.radius;
  const height = heightArg ?? spec.standingHeight;
  const [ox, oy, oz] = origin;
  const [dx, dy, dz] = direction;
  const [fx, fy, fz] = feet;
  const px = ox - fx;
  const py = oy - fy;

  const a = dx * dx + dy * dy;
  const c = px * px + py * py - radius * radius;
  let enter: number;
  let exit: number;
  if (a > 1e-9) {
    const b = 2 * (px * dx + py * dy);
    const disc = b * b - 4 * a * c;
    if (disc < 0) return null;
    const root = Math.sqrt(disc);
    enter = (-b - root) / (2 * a);
    exit = (-b + root) / (2 * a);
  } else if (c > 0) {
    // Travelling vertically and outside the cylinder: never enters it.
    return null;
  } else {
    enter = -Infinity;
    exit = Infinity;
  }

  const z0 = fz;
  const z1 = fz + height;
  if (Math.abs(dz) > 1e-9) {
    let tz0 = (z0 - oz) / dz;
    let tz1 = (z1 - oz) / dz;
    if (tz0 > tz1) [tz0, tz1] = [tz1, tz0];
    enter = Math.max(enter, tz0);
    exit = Math.min(exit, tz1);
  } else if (oz < z0 || oz > z1) {
    return null;
  }

  if (enter > exit || exit < 0) return null;
  // A negative entry with a positive exit means the muzzle is already inside
  // them — point blank, which is a hit at zero distance, not a miss.
  return Math.max(enter, 0);
}

/**
 * Damage after falloff: full out to `falloffStart`, tapering to half at `range`.
 *
 * `falloffStart` is not served — the browser has never needed it, because the
 * server does this arithmetic in a match. The training range does need it, and
 * rather than widen the wire for a number only training reads, it is derived
 * from the two that *are* served. Weapons whose falloff begins at their range
 * (the sniper, the knife) are flat either way, which is the case that matters.
 */
export function damageAt(weapon: WeaponSpec, distance: number, falloffStart: number): number {
  if (distance <= falloffStart || weapon.range <= falloffStart) return weapon.damage;
  const span = weapon.range - falloffStart;
  const t = Math.min(1, (distance - falloffStart) / span);
  return weapon.damage * (1 - 0.5 * t);
}

/**
 * Perturb an aim direction inside a cone of half-angle `spread`.
 *
 * Sampled uniformly over the cone's *area* (hence the square root) rather than
 * uniformly in angle, which would cluster every pellet at the centre and make a
 * shotgun behave like a rifle at range.
 *
 * `rand` is injected so a test can pin the cone with a known sequence; the
 * server's equivalent takes a seeded `random.Random` for the same reason.
 */
/**
 * The absolute `[yaw, pitch]` offset for the `index`-th shot of a burst.
 *
 * A direct port of `weapons.spray_offset`, over the **served** table — so there
 * is one copy of the numbers themselves and this is only the lookup.
 *
 * Held at the last entry past the end of the table rather than wrapping: a
 * pattern that restarted mid-magazine would be unlearnable, which defeats the
 * only reason it is a pattern.
 */
export function sprayOffset(weapon: WeaponSpec, index: number): [number, number] {
  const table = weapon.spray;
  if (!table || table.length === 0) return [0, 0];
  return table[Math.min(Math.max(index, 0), table.length - 1)];
}

/**
 * Add a pattern offset to a shot's view angles.
 *
 * **In view angles, not in the direction vector** — `weapons.apply_spray`'s own
 * rule, and the reason this is a function rather than two additions at the call
 * site: the number the server adds to a shot has to be bit-for-bit the number
 * the client adds to its camera, or the crosshair drifts away from where the
 * bullets go and the pattern stops being learnable.
 *
 * Pitch is clamped just short of vertical. A real pattern is a small climb and
 * can never reach it, but a table edited to something silly should bend the aim
 * rather than flip it over the pole.
 */
export function applySpray(
  yaw: number,
  pitch: number,
  offset: [number, number],
): [number, number] {
  const limit = Math.PI / 2 - 1e-4;
  return [yaw + offset[0], Math.max(-limit, Math.min(limit, pitch + offset[1]))];
}

/**
 * The random cone a shot still gets once the pattern has aimed it.
 *
 * Falls back to the weapon's own cone for anything with no pattern, so this is
 * the one function every caller can ask and there is no branch at the call site.
 * `weapons.residual_spread`.
 */
export function residualSpread(weapon: WeaponSpec, scoped = 0): number {
  const patterned = weapon.spray !== undefined && weapon.spray.length > 0;
  const own = scoped > 0 ? weapon.spread : weapon.hipfireSpread;
  if (!patterned || weapon.residualSpread === undefined) return own;
  // Scoping tightens, and must never *loosen* a cone the pattern already
  // narrowed. No weapon is both scoped and patterned today; the rule is stated
  // rather than assumed.
  return Math.min(weapon.residualSpread, own);
}

export function spreadVector(
  direction: Vec,
  spread: number,
  rand: () => number = Math.random,
): Vec {
  if (spread <= 0) return direction;
  const [dx, dy, dz] = direction;
  // Any vector not parallel to the aim gives a usable first basis vector.
  const [ax, ay, az]: Vec = Math.abs(dz) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  let ux = dy * az - dz * ay;
  let uy = dz * ax - dx * az;
  let uz = dx * ay - dy * ax;
  const ul = Math.hypot(ux, uy, uz) || 1;
  ux /= ul;
  uy /= ul;
  uz /= ul;
  const vx = dy * uz - dz * uy;
  const vy = dz * ux - dx * uz;
  const vz = dx * uy - dy * ux;

  const angle = spread * Math.sqrt(rand());
  const phi = rand() * Math.PI * 2;
  const sa = Math.sin(angle);
  const ca = Math.cos(angle);
  const cphi = Math.cos(phi);
  const sphi = Math.sin(phi);
  const ox = ca * dx + sa * (cphi * ux + sphi * vx);
  const oy = ca * dy + sa * (cphi * uy + sphi * vy);
  const oz = ca * dz + sa * (cphi * uz + sphi * vz);
  const length = Math.hypot(ox, oy, oz) || 1;
  return [ox / length, oy / length, oz / length];
}
