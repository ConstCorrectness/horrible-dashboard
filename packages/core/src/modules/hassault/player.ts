/**
 * First-person movement and collision against the cube grid.
 *
 * Deliberately not a physics engine: the world is a heightfield of columns, so
 * "can I stand here" is a cheap query over the handful of cells the player's
 * circle overlaps. Kept free of three imports so it can be unit-tested headless.
 *
 * Axes follow `world.ts`: `x`/`y` are cube-grid coordinates and `z` is height.
 *
 * ### The movement model
 *
 * Movement carries **momentum**: velocity is integrated against the grid rather
 * than the position being stepped by a direction. Three of the mechanics this
 * game is for have nowhere to live otherwise — weapon recoil pushing the shooter
 * (AssaultCube's shoot-jump) is an impulse, the chained-jump boost multiplies a
 * speed that has to already exist, and the difference between ground control and
 * air momentum *is* the difference between two friction constants.
 *
 * The constants come from AC's `physics.cpp`/`entity.h`, converted out of its
 * per-millisecond units into cubes and seconds. `physics.py` carries the full
 * derivation and the two deliberate deviations (an exponential rather than
 * linear blend, so the rules are frame-rate independent; and a chain-boost
 * window measured from landing rather than from the previous jump). This file is
 * its mirror — change one and you must change both, and
 * `__tests__/physics-vectors.json` is what fails if you don't.
 */
import { currentHitbox, eyeAt, heightAt } from './hitbox';
import { PLAYER_ABOVE_EYE, PLAYER_EYE_HEIGHT, World } from './world';

/** Cubes per second at a walk. Tuned to feel like AC rather than derived from it. */
export const MOVE_SPEED = 22;
export const GRAVITY = 55;
export const JUMP_SPEED = 19;

/**
 * How high a step the player walks up without jumping.
 *
 * Without this, every heightfield cell is a wall: sloped terrain is stored as a
 * series of small floor changes, and a collision test that rejects any rise at
 * all leaves the player stuck on flat-looking ground.
 */
export const STEP_HEIGHT = 1.6;

/**
 * Total body height standing — what headroom is reserved for and what a shot hits.
 *
 * The *default* body, and what `physics-vectors.json` was generated against. The
 * live one comes from `hitbox.ts`, which the server pushes in at join time; the
 * two are identical until somebody tunes one.
 */
export const STANDING_HEIGHT = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE;

/** Crouching: the eye drops to 3/4 (AC's `updatecrouch`), `aboveeye` does not,
 * so the body is ~1.1 cubes shorter and fits under gaps a standing one cannot. */
export const CROUCH_EYE_SCALE = 0.75;
export const CROUCH_EYE_HEIGHT = PLAYER_EYE_HEIGHT * CROUCH_EYE_SCALE;
export const CROUCH_HEIGHT = CROUCH_EYE_HEIGHT + PLAYER_ABOVE_EYE;
/** AC's `chspeed`. The cost that makes moving silently a trade, not an upgrade. */
export const CROUCH_SPEED_SCALE = 0.4;
/** Seconds for a full stand↔crouch transition. */
export const CROUCH_TRANSITION = 0.15;

/** Velocity convergence per second: AC's friction 6 on the floor, 30 in the air,
 * expressed as `50/friction`. Ground settles in ~0.12 s, air in ~0.6 s. */
export const GROUND_RESPONSE = 50 / 6;
export const AIR_RESPONSE = 50 / 30;

/** Gravity ramps with time in air (AC's `dropf`), capped so a long drop is a fall
 * rather than a teleport. */
export const GRAVITY_RAMP = 1.0;
export const MAX_GRAVITY_SCALE = 2.5;

/** The chained-jump boost: jump again within the window while strafing for 25%
 * more speed, capped at 125% of run speed. Both numbers are AC's. */
export const JUMP_CHAIN_WINDOW = 0.25;
export const JUMP_CHAIN_BOOST = 1.25;

/** Landing harder than this costs health. A flat jump lands at `JUMP_SPEED`, so
 * ordinary movement is free and a recoil-launched climb is not. */
export const FALL_SAFE_SPEED = 34;
export const FALL_DAMAGE_PER_SPEED = 3;

export interface PlayerState {
  x: number;
  y: number;
  /** Height of the player's **feet**, not the eye. */
  z: number;
  velX: number;
  velY: number;
  velZ: number;
  yaw: number; // radians, 0 = +x
  pitch: number; // radians, clamped to just under ±90°
  onGround: boolean;
  /** Crouch animation, 0 standing to 1 fully crouched. */
  crouch: number;
  /** What the last input asked for, so the *transition* into a crouch is
   * detectable — which is what `crouchedInAir` keys off. */
  crouchHeld: boolean;
  /** Crouch began airborne: AC leaves such a player at full speed, so a
   * crouch-jump clears a gap without paying the crouch penalty. */
  crouchedInAir: boolean;
  timeInAir: number;
  /** Simulated seconds advanced. A clock local to the simulation, so the
   * jump-chain window means the same on both sides without trusting a wall clock. */
  t: number;
  /** `t` of the last landing — where the chain-boost window is measured from. */
  landedAt: number;
  /** Impact speed of a landing that happened *this step*, else 0. An output: the
   * server turns it into damage and the client only flinches. */
  fallSpeed: number;
}

export interface MoveInput {
  forward: number; // -1..1
  strafe: number; // -1..1
  jump: boolean;
  crouch: boolean;
  /** Ignore gravity and walls — useful for looking around a map. */
  noclip: boolean;
}

/** Radians turned per pixel of mouse movement at sensitivity 1. */
export const LOOK_RADIANS_PER_PIXEL = 0.0022;

export function createPlayer(x: number, y: number, z: number, yaw = 0): PlayerState {
  return {
    x,
    y,
    z,
    velX: 0,
    velY: 0,
    velZ: 0,
    yaw,
    pitch: 0,
    onGround: false,
    crouch: 0,
    crouchHeld: false,
    crouchedInAir: false,
    timeInAir: 0,
    t: 0,
    landedAt: -999,
    fallSpeed: 0,
  };
}

/** Just under a right angle: exactly ±90° makes the view flip over. */
export function clampPitch(pitch: number): number {
  const limit = Math.PI / 2 - 0.001;
  return Math.max(-limit, Math.min(limit, pitch));
}

/**
 * Turn the view by a mouse movement, in raw `movementX`/`movementY` pixels.
 *
 * **Mouse right must increase yaw**, and getting that backwards is not obvious
 * from the camera code: `yaw` is measured about cube +x, but the renderer maps
 * cube `y` onto three's `z`, which reflects the plane — so the intuition that
 * "positive angle turns the way it does on graph paper" is wrong here by exactly
 * one sign. The fixed point is `step`: it walks toward `(cos yaw, sin yaw)`, and
 * the camera's right vector at any yaw is `(-sin yaw, cos yaw)`, so turning right
 * is turning *toward* larger yaw. Pinned by `__tests__/look.test.ts`.
 */
export function applyLook(
  player: PlayerState,
  movementX: number,
  movementY: number,
  sensitivity = 1,
): void {
  const scale = LOOK_RADIANS_PER_PIXEL * Math.max(0, sensitivity);
  player.yaw += movementX * scale;
  player.pitch = clampPitch(player.pitch - movementY * scale);
}

/** Total height of the body right now, mid-crouch included.
 *
 * Reads the live spec: this is one of the two numbers a shot is resolved against,
 * so it is exactly what a tuned body has to be able to move. */
export function bodyHeight(player: PlayerState): number {
  return heightAt(player.crouch);
}

/** The eye's height *above the feet*: where the camera sits and where a shot
 * leaves from, so it must be the same number in both places. */
export function eyeOffset(player: PlayerState): number {
  return eyeAt(player.crouch);
}

/** The absolute eye position, which is what the camera actually uses. */
export function eyeHeight(player: PlayerState): number {
  return player.z + eyeOffset(player);
}

/**
 * The highest floor under the player's circle, and the lowest ceiling over it.
 *
 * Takes the extremes rather than the centre cell's values: standing at the lip of
 * a ledge, the centre may be over thin air while the body is supported, and the
 * extremes are what stop the player sinking or clipping into a low ceiling.
 */
function support(world: World, x: number, y: number) {
  const { x0, x1, y0, y1 } = world.cellsInRadius(x, y, currentHitbox().radius);
  let highestFloor = -Infinity;
  let lowestCeil = Infinity;
  let anySolid = false;
  for (let cy = y0; cy <= y1; cy++) {
    for (let cx = x0; cx <= x1; cx++) {
      if (world.isSolid(cx, cy)) {
        anySolid = true;
        continue;
      }
      highestFloor = Math.max(highestFloor, world.floorAt(cx, cy));
      lowestCeil = Math.min(lowestCeil, world.ceilAt(cx, cy));
    }
  }
  if (highestFloor === -Infinity) {
    // Entirely inside solid geometry — report a floor at the player's feet so
    // they are pushed out rather than dropped through the world.
    return { floor: 0, ceil: Infinity, anySolid, enclosed: true };
  }
  return { floor: highestFloor, ceil: lowestCeil, anySolid, enclosed: false };
}

/**
 * Whether a body of `height` fits at `(x, y)` with its feet at `z`.
 *
 * Three ways to fail: overlapping a solid cell, a floor more than one step above
 * the feet, or a ceiling too low. `height` is a parameter rather than the
 * standing constant because that is exactly what crouching changes — and it is
 * also how "you cannot stand up in here" is decided.
 */
export function canStand(
  world: World,
  x: number,
  y: number,
  z: number,
  height?: number,
): boolean {
  // Optional rather than defaulted to the standing constant: a default argument
  // is evaluated per call in TS, but the constant it would name binds at module
  // load, so a tuned standing height would silently never reach the callers that
  // omit it — tuning that appears to work everywhere except where it decides a hit.
  const spec = currentHitbox();
  const bodyH = height ?? spec.standingHeight;
  const { x0, x1, y0, y1 } = world.cellsInRadius(x, y, spec.radius);
  for (let cy = y0; cy <= y1; cy++) {
    for (let cx = x0; cx <= x1; cx++) {
      if (world.isSolid(cx, cy)) return false;
      if (world.floorAt(cx, cy) > z + STEP_HEIGHT) return false;
      if (world.ceilAt(cx, cy) < z + bodyH) return false;
    }
  }
  return true;
}

/**
 * Add an external kick to a body's velocity.
 *
 * The one way anything outside this file moves a player, and it exists for
 * exactly one caller: weapon recoil. Clearing `onGround` on an upward kick is
 * what makes a shoot-jump work at all — otherwise the vertical resolve at the end
 * of the next step lands the player again immediately, before the velocity has
 * moved them anywhere. Mirrors `apply_impulse` in `physics.py`.
 */
export function applyImpulse(player: PlayerState, dx: number, dy: number, dz: number): void {
  player.velX += dx;
  player.velY += dy;
  player.velZ += dz;
  if (dz > 0) player.onGround = false;
}

/**
 * Advance the crouch animation, and refuse to stand up under a low ceiling.
 *
 * Reads `onGround` from the previous step, as AC's `updatecrouch` reads
 * `onfloor` — the alternative is resolving crouch after movement, which would let
 * a body change height *after* the collision test that admitted it.
 */
function updateCrouch(world: World, player: PlayerState, input: MoveInput, dt: number): void {
  if (input.crouch && !player.crouchHeld && !player.onGround) player.crouchedInAir = true;
  player.crouchHeld = input.crouch;

  let target: number;
  if (input.crouch) target = 1;
  else if (canStand(world, player.x, player.y, player.z, currentHitbox().standingHeight))
    target = 0;
  // Nowhere to stand up into. Holding the current crouch beats popping the body
  // through a ceiling, and it is why crouch is worth binding to a hold rather
  // than a toggle in tight geometry.
  else target = player.crouch;

  const rate = CROUCH_TRANSITION > 0 ? dt / CROUCH_TRANSITION : 1;
  player.crouch =
    target > player.crouch
      ? Math.min(target, player.crouch + rate)
      : Math.max(target, player.crouch - rate);
}

/**
 * Unit direction the player is asking to move in, in grid coordinates.
 *
 * Normalised, so forward-plus-strafe is not 1.41x faster than forward alone.
 * Diagonal overspeed is the accidental version of a movement tech; this game has
 * a deliberate one (the chain boost) and does not need both.
 */
function wishDirection(player: PlayerState, input: MoveInput): [number, number] {
  const sin = Math.sin(player.yaw);
  const cos = Math.cos(player.yaw);
  const dx = cos * input.forward - sin * input.strafe;
  const dy = sin * input.forward + cos * input.strafe;
  const length = Math.hypot(dx, dy);
  if (length < 1e-9) return [0, 0];
  return [dx / length, dy / length];
}

/**
 * Advance the player by `dt` seconds.
 *
 * Horizontal movement is resolved **one axis at a time** so a blocked direction
 * slides along the wall instead of stopping dead — testing the combined vector
 * once would make every corner sticky.
 */
export function step(world: World, player: PlayerState, input: MoveInput, dt: number): void {
  // Clamp the timestep: a backgrounded tab returns a huge dt, and integrating it
  // in one go teleports the player through walls.
  dt = Math.min(dt, 0.1);
  if (dt <= 0) {
    player.fallSpeed = 0;
    return;
  }

  if (input.noclip) {
    // Sightseeing: no gravity, no walls, no momentum. Offline only — the server
    // has no such move, so in a match it would desync on the first frame.
    const [wx, wy] = wishDirection(player, input);
    player.x += wx * MOVE_SPEED * dt;
    player.y += wy * MOVE_SPEED * dt;
    player.z += input.jump ? MOVE_SPEED * dt : 0;
    player.velX = 0;
    player.velY = 0;
    player.velZ = 0;
    player.onGround = false;
    player.fallSpeed = 0;
    player.t += dt;
    return;
  }

  player.t += dt;
  // An output of this step only. Cleared first so a step with no landing in it
  // cannot report the previous one's impact a second time.
  player.fallSpeed = 0;

  updateCrouch(world, player, input, dt);

  // -- horizontal: converge on the wish velocity ------------------------------
  //
  // Crouched speed is AC's `chspeed`: 0.4 on the floor, and 0.4 in the air too
  // *unless* the crouch began airborne, which is the crouch-jump exemption.
  const scale =
    player.crouch > 0.5 && (player.onGround || !player.crouchedInAir) ? CROUCH_SPEED_SCALE : 1;
  const speedCap = MOVE_SPEED * scale;

  const [wx, wy] = wishDirection(player, input);
  const response = player.onGround ? GROUND_RESPONSE : AIR_RESPONSE;
  const blend = 1 - Math.exp(-response * dt);
  player.velX += (wx * speedCap - player.velX) * blend;
  player.velY += (wy * speedCap - player.velY) * blend;

  // -- jump, and the chained-jump boost ---------------------------------------
  if (input.jump && player.onGround) {
    if (input.strafe !== 0 && player.t - player.landedAt <= JUMP_CHAIN_WINDOW) {
      const speed = Math.hypot(player.velX, player.velY);
      if (speed > 0.1) {
        // 25% faster, but never past 125% of run speed: AC's
        // `1.25/max(speed/fullspeed, 1)`, a boost below the cap and a clamp above.
        const factor = JUMP_CHAIN_BOOST / Math.max(speed / MOVE_SPEED, 1);
        player.velX *= factor;
        player.velY *= factor;
      }
    }
    player.velZ = JUMP_SPEED;
    player.onGround = false;
    player.timeInAir = 0;
  }

  // -- horizontal: move, one axis at a time -----------------------------------
  //
  // A refused axis loses its velocity: keeping it would store up a shove that
  // fires the instant the body clears the wall.
  const height = bodyHeight(player);
  const dx = player.velX * dt;
  const dy = player.velY * dt;
  if (dx !== 0) {
    if (canStand(world, player.x + dx, player.y, player.z, height)) player.x += dx;
    else player.velX = 0;
  }
  if (dy !== 0) {
    if (canStand(world, player.x, player.y + dy, player.z, height)) player.y += dy;
    else player.velY = 0;
  }

  // Resolved before gravity, not after: `support` reads only x and y, and
  // checking afterwards means a wedged player has already been moved down by one
  // frame of falling — which does not look like falling, it looks like sinking
  // half a cube a second forever.
  const { floor, ceil, enclosed } = support(world, player.x, player.y);
  if (enclosed) {
    // Wedged in solid geometry: hold still so the player can walk back out.
    player.velX = 0;
    player.velY = 0;
    player.velZ = 0;
    player.onGround = true;
    return;
  }

  // -- vertical ---------------------------------------------------------------
  //
  // Whether the body was already resting on the floor when this step began —
  // read *after* the jump, which clears it. Both branches below need it, and for
  // the same reason: "arrived on the ground" and "was already on the ground" are
  // different events, and conflating them costs the game two mechanics. A resting
  // body dips below the floor under gravity every single frame, so treating that
  // as a landing would reset the chain-boost window continuously (making the
  // timing free) and charge fall damage for standing still; and a body genuinely
  // falling passes through the snap-down band on its way in, so treating that as
  // a snap would mean nothing ever lands.
  const wasGrounded = player.onGround;

  player.timeInAir = wasGrounded ? 0 : player.timeInAir + dt;
  // Gravity ramps with time in air, as AC's `dropf` does, so a fall comes down
  // harder than the jump went up.
  const gravity = GRAVITY * Math.min(MAX_GRAVITY_SCALE, 1 + player.timeInAir / GRAVITY_RAMP);
  player.velZ -= gravity * dt;
  player.z += player.velZ * dt;

  if (player.z <= floor) {
    player.z = floor;
    if (!wasGrounded) {
      // A real landing. Reported for this step only; the server turns the impact
      // into damage, and the window this opens is what a chained jump has to be
      // timed against.
      player.fallSpeed = player.velZ < 0 ? -player.velZ : 0;
      player.landedAt = player.t;
    }
    player.velZ = 0;
    player.onGround = true;
    player.timeInAir = 0;
    // On the floor, so the crouch-jump exemption is spent.
    player.crouchedInAir = false;
  } else if (wasGrounded && player.velZ <= 0 && player.z - floor <= STEP_HEIGHT * 0.5) {
    // Walking off a small lip shouldn't launch the player into a fall: snap down.
    // Not a landing — nothing was fallen, so it costs no health and opens no
    // chain-boost window that was never earned.
    player.z = floor;
    player.velZ = 0;
    player.onGround = true;
    player.timeInAir = 0;
    player.crouchedInAir = false;
  } else {
    player.onGround = false;
  }
  if (player.z + height > ceil) {
    player.z = Math.max(floor, ceil - height);
    if (player.velZ > 0) player.velZ = 0;
  }
}

/**
 * Health cost of landing at `impact` cubes per second.
 *
 * Zero for anything a jump can produce, then linear. Mirrors `fall_damage` in
 * `physics.py` so the HUD can show what a drop is about to cost without a second
 * copy of the rule.
 */
export function fallDamage(impact: number): number {
  if (impact <= FALL_SAFE_SPEED) return 0;
  return (impact - FALL_SAFE_SPEED) * FALL_DAMAGE_PER_SPEED;
}

/**
 * Place a player on a spawn point, standing on the ground beneath it.
 *
 * **A `playerstart`'s `z` is not the ground.** It is the mapper's own origin at
 * the moment they typed `/newent playerstart`, and in Cube 1 that origin is the
 * *eye*, not the feet — which is why the single most common value across the
 * 1741 official spawns is exactly four above the floor the body rests on
 * (`(int)(floor + 4.5)`, truncated into the `short` the format stores). Nor is it
 * reliable even read that way: AC's editor flies, so the rest are scattered from
 * one to twenty-two cubes up with no relation to anything. The engine gets away
 * with it because `entinmap` and gravity resolve the spawn on arrival.
 *
 * So the height comes from the world instead. `support` is the same query `step`
 * resolves against, which makes this its fixed point: a player spawned here is
 * already exactly where their first frame would put them, rather than falling
 * several cubes into it.
 *
 * Mirrored by `spawn_at` in `backend/modules/hassault/physics.py` and pinned
 * against it by `physics-vectors.json`.
 */
export function spawnAt(
  world: World,
  spawn: { x: number; y: number; z: number; yaw: number | null },
): PlayerState {
  const x = spawn.x + 0.5;
  const y = spawn.y + 0.5;
  const { floor, enclosed } = support(world, x, y);
  // Every cell under the body solid — which no official map manages, but a
  // community one might. The centre cell's floor is the best guess left.
  const z = enclosed ? world.floorAt(Math.floor(spawn.x), Math.floor(spawn.y)) : floor;
  // Entity yaw is degrees clockwise from north; the camera uses radians about +x.
  const yaw = ((spawn.yaw ?? 0) * Math.PI) / 180;
  const player = createPlayer(x, y, z, yaw);
  // Resting on the floor, so say so: otherwise the very first frame refuses a
  // jump that the player is standing in a perfectly good position to make.
  player.onGround = true;
  return player;
}
