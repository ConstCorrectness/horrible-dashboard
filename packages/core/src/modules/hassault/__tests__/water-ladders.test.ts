/**
 * Water and ladders, on the client side of the duplicated simulation.
 *
 * `conformance.test.ts` pins that this file's `step` agrees with the server's;
 * what these pin is that the rules are the intended ones. Same division of
 * labour as `movement.test.ts` — agreement and correctness are different
 * arguments, and the fixture can only make the first.
 */
import { describe, expect, it } from 'vitest';

import type { MapEntity, MapInfo } from '../api';
import {
  createPlayer,
  fallDamage,
  inWater,
  ladderAt,
  MOVE_SPEED,
  step,
  submerged,
  SWIM_SPEED,
  WATER_SPEED_SCALE,
  type MoveInput,
  type PlayerState,
} from '../player';
import { LADDER_ENTITY, SPACE, World } from '../world';

const PLANES = ['type', 'floor', 'ceil', 'wtex', 'ftex', 'ctex', 'vdelta', 'utex', 'tag'];

interface Opts {
  ssize?: number;
  floor?: number;
  ceil?: number;
  waterlevel?: number;
  ladders?: { x: number; y: number; height: number }[];
}

/** An open room, optionally flooded and optionally with a ladder in it. */
function world({
  ssize = 32,
  floor = 0,
  ceil = 60,
  waterlevel = -100,
  ladders = [],
}: Opts = {}): World {
  const n = ssize * ssize;
  const buf = new ArrayBuffer(n * PLANES.length);
  const plane = (name: string) => {
    const off = PLANES.indexOf(name) * n;
    return name === 'floor' || name === 'ceil'
      ? new Int8Array(buf, off, n)
      : new Uint8Array(buf, off, n);
  };
  plane('type').fill(SPACE);
  plane('floor').fill(floor);
  plane('ceil').fill(ceil);

  const entities: MapEntity[] = ladders.map((l) => ({
    type: LADDER_ENTITY,
    name: 'ladder',
    x: l.x,
    y: l.y,
    z: 0,
    yaw: null,
    attrs: [l.height, 0, 0, 0, 0, 0, 0],
  }));

  return new World(
    {
      name: 'test',
      title: 'test',
      magic: 'ACMP',
      version: 10,
      sfactor: Math.log2(ssize),
      ssize,
      cubic_size: n,
      waterlevel,
      watercolor: [0, 0, 0, 0],
      maprevision: 1,
      ambient: 0,
      flags: 0,
      timestamp: 0,
      entity_count: entities.length,
      entities,
      spawns: {},
      truncated: false,
      legacy_unscaled_attrs: false,
      plane_order: PLANES,
      items: [],
    } as unknown as MapInfo,
    buf,
  );
}

function input(over: Partial<MoveInput> = {}): MoveInput {
  return { forward: 0, strafe: 0, jump: false, crouch: false, noclip: false, ...over };
}

function walk(w: World, player: PlayerState, steps: number, over: Partial<MoveInput> = {}): void {
  for (let i = 0; i < steps; i += 1) step(w, player, input(over), 1 / 60);
}

describe('water', () => {
  it('is read at the feet and swimming at the eye', () => {
    const player = createPlayer(8, 8, 0);
    expect(inWater(world({ waterlevel: 3 }), player)).toBe(true);
    // The trap this pins: `eyeHeight` here is absolute and already includes `z`,
    // unlike `eye_height` in physics.py. Adding `z` to it reads as submerged far
    // too late, which shows up as a swimmer who sinks when the server says rise.
    expect(submerged(world({ waterlevel: 3 }), player)).toBe(false);
    expect(submerged(world({ waterlevel: 6 }), player)).toBe(true);
  });

  it('wades slower than it walks', () => {
    const dry = createPlayer(8, 8, 0);
    const wet = createPlayer(8, 8, 0);
    dry.onGround = true;
    wet.onGround = true;
    walk(world(), dry, 60, { forward: 1 });
    walk(world({ waterlevel: 3 }), wet, 60, { forward: 1 });
    expect(wet.x).toBeLessThan(dry.x);
    expect(Math.hypot(wet.velX, wet.velY)).toBeCloseTo(MOVE_SPEED * WATER_SPEED_SCALE, 0);
  });

  it('makes jump the swim control when submerged', () => {
    const player = createPlayer(8, 8, 0);
    player.onGround = true;
    step(world({ waterlevel: 9 }), player, input({ jump: true }), 1 / 60);
    // Not a nineteen-cube-a-second jump off the riverbed.
    expect(player.velZ).toBeLessThan(SWIM_SPEED);
  });

  it('rises on jump, dives on crouch, and dives more slowly', () => {
    const w = world({ waterlevel: 20 });
    const up = createPlayer(8, 8, 6);
    const down = createPlayer(8, 8, 6);
    walk(w, up, 60, { jump: true });
    walk(w, down, 60, { crouch: true });
    expect(up.z).toBeGreaterThan(6);
    expect(down.z).toBeLessThan(6);
    expect(6 - down.z).toBeLessThan(up.z - 6);
  });

  it('takes the fall out of a long drop', () => {
    const w = world({ waterlevel: 12, ceil: 120 });
    const player = createPlayer(8, 8, 100);
    for (let i = 0; i < 600 && !player.onGround; i += 1) step(w, player, input(), 1 / 60);
    expect(player.onGround).toBe(true);
    expect(fallDamage(player.fallSpeed)).toBe(0);
  });

  it('helps in proportion to depth rather than as a switch', () => {
    // An inch of water must not be a total fall-damage negator: that is the
    // puddle-dive that would put shoot-jumping back on a free ride.
    const w = world({ waterlevel: 0.5, ceil: 120 });
    const player = createPlayer(8, 8, 100);
    for (let i = 0; i < 600 && !player.onGround; i += 1) step(w, player, input(), 1 / 60);
    expect(player.fallSpeed).toBeGreaterThan(0);
  });
});

describe('ladders', () => {
  const climb = (height = 20) => world({ ladders: [{ x: 16, y: 16, height }] });

  it('resolves an entity into a span resting on its own floor', () => {
    const w = world({ floor: 6, ladders: [{ x: 16, y: 16, height: 10 }] });
    expect(w.ladders).toEqual([{ x: 16.5, y: 16.5, base: 6, top: 16 }]);
  });

  it('drops a ladder with no height rather than making it unbounded', () => {
    expect(world({ ladders: [{ x: 16, y: 16, height: 0 }] }).ladders).toEqual([]);
  });

  it('catches a body inside its span and no other', () => {
    const w = climb();
    expect(ladderAt(w, createPlayer(16.5, 16.5, 10))).not.toBeNull();
    expect(ladderAt(w, createPlayer(16.5, 16.5, 25))).toBeNull();
    expect(ladderAt(w, createPlayer(24, 16.5, 10))).toBeNull();
  });

  it('climbs when facing the ladder and not when running past it', () => {
    const facing = createPlayer(15.5, 16.5, 0, 0);
    const sideways = createPlayer(15.5, 16.5, 0, Math.PI / 2);
    facing.onGround = true;
    sideways.onGround = true;
    walk(climb(), facing, 30, { forward: 1 });
    walk(climb(), sideways, 30, { forward: 1 });
    expect(facing.z).toBeGreaterThan(2);
    expect(sideways.z).toBeCloseTo(0, 6);
  });

  it('holds position with no input', () => {
    const player = createPlayer(15.5, 16.5, 8, 0);
    walk(climb(), player, 60);
    expect(player.z).toBeCloseTo(8, 6);
  });

  it('descends on back without walking out of the volume', () => {
    const player = createPlayer(15.5, 16.5, 12, 0);
    walk(climb(), player, 30, { forward: -1 });
    expect(player.z).toBeLessThan(12);
    expect(Math.hypot(player.x - 15.5, player.y - 16.5)).toBeLessThan(0.5);
  });

  it('stops at the top rung', () => {
    const player = createPlayer(15.5, 16.5, 10, 0);
    walk(climb(12), player, 120, { forward: 1 });
    expect(player.z).toBeLessThanOrEqual(12 + 1e-9);
  });

  it('never accrues air time, so a climb is never charged as a fall', () => {
    const player = createPlayer(15.5, 16.5, 0, 0);
    player.onGround = true;
    walk(climb(), player, 60, { forward: 1 });
    expect(player.timeInAir).toBe(0);
    expect(player.fallSpeed).toBe(0);
  });

  it('lets go at the top instead of riding back down', () => {
    // Walking forward off the top crosses the ladder's centre, where "toward the
    // ladder" reverses — attached, the input that was climbing up starts
    // climbing down and returns the player to the bottom.
    const player = createPlayer(15.5, 16.5, 12, 0);
    walk(climb(12), player, 40, { forward: 1 });
    expect(player.x).toBeGreaterThan(16.5);
    expect(player.z).toBeLessThan(12);
  });
});
