/**
 * The body a shot is resolved against, client side.
 *
 * The mirror of `backend/modules/hassault/hitbox.py`, and deliberately shaped the
 * same way: the server is the authority, this is what the prediction runs against,
 * and the two must not be able to disagree about how tall a player is.
 *
 * **Injected, never fetched.** `world.ts` and `player.ts` are three-free and
 * import-free on purpose so the conformance suite can replay
 * `__tests__/physics-vectors.json` headless against the same code the game runs.
 * A module that called `fetch` here would end that, so the session pushes the
 * served spec in with `setHitbox` at join time and this file stays pure.
 *
 * The exported `DEFAULT_HITBOX` is the shipped body — what the vectors were
 * generated against, and what prediction uses until the server says otherwise.
 */

/** One body, in cubes. Field-for-field the server's `HitboxSpec.to_dict()`. */
export interface HitboxSpec {
  /** Content hash of the hit-deciding dimensions. Shown in the tuning lab, and
   * stamped into `physics-vectors.json` so a stale fixture cannot pass. */
  specId: string;
  /** Only `cylinder` exists today. Carried so a client can refuse a shape it does
   * not know how to draw, rather than drawing the wrong one silently. */
  shape: string;
  radius: number;
  eyeHeight: number;
  aboveEye: number;
  standingHeight: number;
  crouchEyeScale: number;
  crouchEyeHeight: number;
  crouchHeight: number;
  /** Crouched height as a fraction of standing — what an avatar is squashed by. */
  crouchScale: number;
  /** Top band of the body that takes the weapon's head multiplier. */
  headBand: number;
  fitTolerance: number;
  eyeTolerance: number;
}

/**
 * The shipped body: AssaultCube's `entity.h` defaults.
 *
 * Derived values are spelled out rather than computed, because the server serves
 * them computed — two implementations of `crouchHeight` is two chances to round it
 * differently, which is precisely the class of drift this module exists to close.
 */
export const DEFAULT_HITBOX: HitboxSpec = {
  specId: '86d9f2779917',
  shape: 'cylinder',
  radius: 1.1,
  eyeHeight: 4.5,
  aboveEye: 0.7,
  standingHeight: 5.2,
  crouchEyeScale: 0.75,
  crouchEyeHeight: 3.375,
  crouchHeight: 4.075,
  crouchScale: 4.075 / 5.2,
  headBand: 1.0,
  fitTolerance: 0.35,
  eyeTolerance: 0.15,
};

let active: HitboxSpec = DEFAULT_HITBOX;

/** The spec in force. Read per call, never captured in a default argument — a
 * default binds at module load, so a tuned body would reach everything except the
 * code that decides the hit. */
export function currentHitbox(): HitboxSpec {
  return active;
}

/**
 * Adopt the served spec.
 *
 * Called once when a session starts, and again when the tuning lab changes the
 * body. A `null` restores the shipped one, which is what a failed fetch should do:
 * predicting against the default is wrong only if somebody has tuned it, whereas
 * predicting against nothing does not work at all.
 */
export function setHitbox(spec: HitboxSpec | null): HitboxSpec {
  active = spec ?? DEFAULT_HITBOX;
  return active;
}

/** Body height mid-crouch, `crouch` being the 0..1 animation fraction. */
export function heightAt(crouch: number, spec: HitboxSpec = active): number {
  return spec.standingHeight + (spec.crouchHeight - spec.standingHeight) * crouch;
}

/** Eye height mid-crouch — where the camera sits and where a shot leaves from. */
export function eyeAt(crouch: number, spec: HitboxSpec = active): number {
  return spec.eyeHeight + (spec.crouchEyeHeight - spec.eyeHeight) * crouch;
}
