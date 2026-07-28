/**
 * First-person movement and collision against the cube grid.
 *
 * Deliberately not a physics engine: the world is a heightfield of columns, so
 * "can I stand here" is a cheap query over the handful of cells the player's
 * circle overlaps. Kept free of three imports so it can be unit-tested headless.
 *
 * Axes follow `world.ts`: `x`/`y` are cube-grid coordinates and `z` is height.
 */
import { PLAYER_ABOVE_EYE, PLAYER_EYE_HEIGHT, PLAYER_RADIUS, World } from './world';

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

export interface PlayerState {
  x: number;
  y: number;
  /** Height of the player's **feet**, not the eye. */
  z: number;
  velZ: number;
  yaw: number; // radians, 0 = +x
  pitch: number; // radians, clamped to just under ±90°
  onGround: boolean;
}

export interface MoveInput {
  forward: number; // -1..1
  strafe: number; // -1..1
  jump: boolean;
  /** Ignore gravity and walls — useful for looking around a map. */
  noclip: boolean;
}

export function createPlayer(x: number, y: number, z: number, yaw = 0): PlayerState {
  return { x, y, z, velZ: 0, yaw, pitch: 0, onGround: false };
}

/** The eye position, which is what the camera actually uses. */
export function eyeHeight(player: PlayerState): number {
  return player.z + PLAYER_EYE_HEIGHT;
}

/**
 * The highest floor under the player's circle, and the lowest ceiling over it.
 *
 * Takes the extremes rather than the centre cell's values: standing at the lip of
 * a ledge, the centre may be over thin air while the body is supported, and the
 * extremes are what stop the player sinking or clipping into a low ceiling.
 */
function support(world: World, x: number, y: number) {
  const { x0, x1, y0, y1 } = world.cellsInRadius(x, y, PLAYER_RADIUS);
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
 * Whether the player's body fits at `(x, y)` with feet at `z`.
 *
 * Three ways to fail: overlapping a solid cell, a floor more than one step above
 * the feet, or a ceiling too low to stand under.
 */
export function canStand(world: World, x: number, y: number, z: number): boolean {
  const { x0, x1, y0, y1 } = world.cellsInRadius(x, y, PLAYER_RADIUS);
  const headroom = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE;
  for (let cy = y0; cy <= y1; cy++) {
    for (let cx = x0; cx <= x1; cx++) {
      if (world.isSolid(cx, cy)) return false;
      if (world.floorAt(cx, cy) > z + STEP_HEIGHT) return false;
      if (world.ceilAt(cx, cy) < z + headroom) return false;
    }
  }
  return true;
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

  const sin = Math.sin(player.yaw);
  const cos = Math.cos(player.yaw);
  const dx = (cos * input.forward - sin * input.strafe) * MOVE_SPEED * dt;
  const dy = (sin * input.forward + cos * input.strafe) * MOVE_SPEED * dt;

  if (input.noclip) {
    player.x += dx;
    player.y += dy;
    player.z += input.jump ? MOVE_SPEED * dt : 0;
    player.velZ = 0;
    player.onGround = false;
    return;
  }

  if (dx !== 0 && canStand(world, player.x + dx, player.y, player.z)) player.x += dx;
  if (dy !== 0 && canStand(world, player.x, player.y + dy, player.z)) player.y += dy;

  // Resolved before gravity, not after: `support` reads only x and y, and
  // checking afterwards means a wedged player has already been moved down by one
  // frame of falling — which does not look like falling, it looks like sinking
  // half a cube a second forever.
  const { floor, ceil, enclosed } = support(world, player.x, player.y);
  if (enclosed) {
    // Wedged in solid geometry: hold still so the player can walk back out.
    player.velZ = 0;
    player.onGround = true;
    return;
  }

  // Vertical: gravity, then resolve against the ground.
  if (input.jump && player.onGround) {
    player.velZ = JUMP_SPEED;
    player.onGround = false;
  }
  player.velZ -= GRAVITY * dt;
  player.z += player.velZ * dt;

  if (player.z <= floor) {
    player.z = floor;
    player.velZ = 0;
    player.onGround = true;
  } else {
    player.onGround = false;
    // Walking off a small lip shouldn't launch the player into a fall; snap down
    // when they are barely above the ground and not moving upward.
    if (player.velZ <= 0 && player.z - floor <= STEP_HEIGHT * 0.5) {
      player.z = floor;
      player.velZ = 0;
      player.onGround = true;
    }
  }
  const headroom = PLAYER_EYE_HEIGHT + PLAYER_ABOVE_EYE;
  if (player.z + headroom > ceil) {
    player.z = Math.max(floor, ceil - headroom);
    if (player.velZ > 0) player.velZ = 0;
  }
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
