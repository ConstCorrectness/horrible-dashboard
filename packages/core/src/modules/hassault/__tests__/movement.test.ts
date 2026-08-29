/**
 * The browser's half of the movement rules.
 *
 * `conformance.test.ts` pins that this file *agrees* with `physics.py`; these pin
 * that the rules are **right**. Both matter, and for different reasons: two subtly
 * different correct-looking implementations desync a match just as thoroughly as
 * one wrong one, but a fixture generated from the server would happily enshrine a
 * rule that is wrong on both sides.
 *
 * The cases are chosen around what the movement is *for* — momentum, the chained
 * jump, crouching, and recoil — because those are the mechanics a refactor can
 * quietly neuter while every position test still passes.
 */
import { describe, expect, it } from 'vitest';

import type { MapInfo } from '../api';
import {
  applyImpulse,
  bodyHeight,
  canStand,
  createPlayer,
  CROUCH_HEIGHT,
  CROUCH_SPEED_SCALE,
  eyeOffset,
  FALL_SAFE_SPEED,
  fallDamage,
  JUMP_CHAIN_BOOST,
  MOVE_SPEED,
  STANDING_HEIGHT,
  step,
  type MoveInput,
  type PlayerState,
} from '../player';
import { PLAYER_EYE_HEIGHT, SOLID, SPACE, World } from '../world';

const PLANES = ['type', 'floor', 'ceil', 'wtex', 'ftex', 'ctex', 'vdelta', 'utex', 'tag'];

/** An open room with a solid border, big enough for the 2.2-cube body to run in. */
function room(ssize = 64, floorAt = 0, ceilAt = 24): World {
  const n = ssize * ssize;
  const buf = new ArrayBuffer(n * PLANES.length);
  const at = (name: string) => {
    const off = PLANES.indexOf(name) * n;
    return name === 'floor' || name === 'ceil'
      ? new Int8Array(buf, off, n)
      : new Uint8Array(buf, off, n);
  };
  const type = at('type');
  const floor = at('floor');
  const ceil = at('ceil');
  type.fill(SOLID);
  for (let y = 2; y < ssize - 2; y++) {
    for (let x = 2; x < ssize - 2; x++) {
      type[y * ssize + x] = SPACE;
      floor[y * ssize + x] = floorAt;
      ceil[y * ssize + x] = ceilAt;
    }
  }
  const info: MapInfo = {
    name: 'room',
    title: 'room',
    magic: 'ACMP',
    version: 10,
    sfactor: Math.log2(ssize),
    ssize,
    cubic_size: n,
    waterlevel: -100,
    watercolor: [0, 0, 0, 0],
    maprevision: 1,
    ambient: 0,
    flags: 0,
    timestamp: 0,
    entity_count: 0,
    entities: [],
    spawns: {},
    truncated: false,
    legacy_unscaled_attrs: false,
    plane_order: PLANES,
    items: [],
  };
  return new World(info, buf);
}

const input = (over: Partial<MoveInput> = {}): MoveInput => ({
  forward: 0,
  strafe: 0,
  jump: false,
  crouch: false,
  noclip: false,
  ...over,
});

function run(world: World, player: PlayerState, frames: number, over: Partial<MoveInput> = {}) {
  for (let i = 0; i < frames; i++) step(world, player, input(over), 1 / 60);
}

const speed = (p: PlayerState) => Math.hypot(p.velX, p.velY);

function grounded(x = 16, y = 16, z = 0): PlayerState {
  const p = createPlayer(x, y, z);
  p.onGround = true;
  return p;
}

describe('momentum', () => {
  it('converges on the speed cap and no further', () => {
    const world = room();
    const player = grounded();
    run(world, player, 120, { forward: 1 });
    expect(speed(player)).toBeCloseTo(MOVE_SPEED, 3);
  });

  it('does not make diagonal movement faster', () => {
    // Diagonal overspeed is the *accidental* version of a movement tech; this game
    // has a deliberate one and does not need both.
    const world = room();
    const straight = grounded();
    const diagonal = grounded();
    run(world, straight, 120, { forward: 1 });
    run(world, diagonal, 120, { forward: 1, strafe: 1 });
    expect(speed(diagonal)).toBeCloseTo(speed(straight), 6);
  });

  it('gives air control far less authority than ground control', () => {
    const world = room(64, 0, 64);
    const ground = grounded();
    ground.velX = MOVE_SPEED;
    const air = createPlayer(16, 16, 30);
    air.velX = MOVE_SPEED;
    // Both ask to stop.
    run(world, ground, 12);
    run(world, air, 12);
    expect(ground.velX).toBeLessThan(MOVE_SPEED * 0.25);
    expect(air.velX).toBeGreaterThan(MOVE_SPEED * 0.7);
    expect(air.velX).toBeGreaterThan(ground.velX * 3);
  });

  it('accelerates a fall harder the longer it lasts', () => {
    const world = room(16, 0, 120);
    const player = createPlayer(8, 8, 110);
    run(world, player, 30);
    const firstHalf = 110 - player.z;
    const before = player.z;
    run(world, player, 30);
    expect(before - player.z).toBeGreaterThan(firstHalf * 1.5);
  });
});

describe('the chained-jump boost', () => {
  it('builds speed past the run cap while strafing, and stops at 125%', () => {
    const world = room(96);
    const player = grounded(20, 20);
    player.yaw = 0.6;
    run(world, player, 30, { forward: 1, strafe: 1 });
    let peak = 0;
    for (let i = 0; i < 180; i++) {
      step(world, player, input({ forward: 1, strafe: 1, jump: true }), 1 / 60);
      peak = Math.max(peak, speed(player));
    }
    expect(peak).toBeGreaterThan(MOVE_SPEED * 1.05);
    // A clamp above the cap, not a multiplier that compounds — so no amount of
    // chaining passes 125%.
    expect(peak).toBeCloseTo(MOVE_SPEED * JUMP_CHAIN_BOOST, 6);
  });

  it('needs strafe', () => {
    const world = room(96);
    const player = grounded(20, 20);
    run(world, player, 30, { forward: 1 });
    let peak = 0;
    for (let i = 0; i < 180; i++) {
      step(world, player, input({ forward: 1, jump: true }), 1 / 60);
      peak = Math.max(peak, speed(player));
    }
    expect(peak).toBeLessThanOrEqual(MOVE_SPEED + 1e-6);
  });

  it('gives a standing jump nothing', () => {
    // The window is measured from a *landing*. A resting body dips below the floor
    // under gravity every frame, and counting that as a landing would reset the
    // window continuously — making the timing free.
    const world = room(96);
    const player = grounded(20, 20);
    run(world, player, 120, { forward: 1, strafe: 1 });
    const before = speed(player);
    step(world, player, input({ forward: 1, strafe: 1, jump: true }), 1 / 60);
    expect(speed(player)).toBeLessThanOrEqual(before + 1e-6);
  });
});

describe('crouching', () => {
  it('shortens the body and lowers the eye', () => {
    const world = room();
    const player = grounded();
    expect(bodyHeight(player)).toBeCloseTo(STANDING_HEIGHT, 9);
    run(world, player, 30, { crouch: true });
    expect(player.crouch).toBeCloseTo(1, 6);
    expect(bodyHeight(player)).toBeCloseTo(CROUCH_HEIGHT, 9);
    expect(eyeOffset(player)).toBeLessThan(PLAYER_EYE_HEIGHT);
  });

  it('costs speed on the ground', () => {
    const world = room();
    const player = grounded();
    run(world, player, 120, { forward: 1, crouch: true });
    expect(speed(player)).toBeCloseTo(MOVE_SPEED * CROUCH_SPEED_SCALE, 3);
  });

  it('costs nothing once already airborne', () => {
    // AC's `crouchedinair` exemption: what makes a crouch-jump a way to clear a gap
    // rather than a way to fall short of it.
    const world = room(64, 0, 64);
    const player = grounded();
    run(world, player, 60, { forward: 1 });
    step(world, player, input({ forward: 1, jump: true }), 1 / 60);
    run(world, player, 20, { forward: 1, crouch: true });
    expect(player.crouch).toBeCloseTo(1, 6);
    expect(speed(player)).toBeGreaterThan(MOVE_SPEED * 0.95);
  });

  it('fits under a ceiling a standing body does not', () => {
    const world = room(16, 0, 5);
    expect(STANDING_HEIGHT).toBeGreaterThan(5);
    expect(CROUCH_HEIGHT).toBeLessThan(5);
    expect(canStand(world, 8, 8, 0, STANDING_HEIGHT)).toBe(false);
    expect(canStand(world, 8, 8, 0, CROUCH_HEIGHT)).toBe(true);
  });

  it('refuses to stand up with no headroom', () => {
    // Otherwise the body pops through the roof, and `support` then shoves it back
    // down forever.
    const world = room(16, 0, 5);
    const player = grounded(8, 8);
    player.crouch = 1;
    run(world, player, 60, { crouch: false });
    expect(player.crouch).toBeCloseTo(1, 6);
  });
});

describe('impulses and fall damage', () => {
  it('leaves the ground on an upward kick', () => {
    // Clearing `onGround` is the whole trick: without it the next step's vertical
    // resolve lands the player again before the velocity moved them anywhere.
    const world = room(16, 0, 64);
    const player = grounded(8, 8);
    applyImpulse(player, 0, 0, 14);
    expect(player.onGround).toBe(false);
    run(world, player, 10);
    expect(player.z).toBeGreaterThan(1);
  });

  it('reaches higher than a plain jump can', () => {
    const world = room(16, 0, 120);
    const plain = grounded(8, 8);
    const boosted = grounded(8, 8);
    step(world, plain, input({ jump: true }), 1 / 60);
    step(world, boosted, input({ jump: true }), 1 / 60);
    applyImpulse(boosted, 0, 0, 12);
    let peakPlain = 0;
    let peakBoosted = 0;
    for (let i = 0; i < 120; i++) {
      step(world, plain, input(), 1 / 60);
      step(world, boosted, input(), 1 / 60);
      peakPlain = Math.max(peakPlain, plain.z);
      peakBoosted = Math.max(peakBoosted, boosted.z);
    }
    expect(peakBoosted).toBeGreaterThan(peakPlain * 1.5);
  });

  it('never charges a flat jump', () => {
    // The threshold has to sit above what ordinary movement produces, or the game
    // charges you for playing it.
    const world = room(16, 0, 64);
    const player = grounded(8, 8);
    step(world, player, input({ jump: true }), 1 / 60);
    let worst = 0;
    for (let i = 0; i < 120; i++) {
      step(world, player, input(), 1 / 60);
      worst = Math.max(worst, player.fallSpeed);
    }
    expect(worst).toBeGreaterThan(0); // it did land
    expect(fallDamage(worst)).toBe(0);
  });

  it('reports a long drop exactly once', () => {
    const world = room(16, 0, 120);
    const player = createPlayer(8, 8, 100);
    const impacts: number[] = [];
    for (let i = 0; i < 240; i++) {
      step(world, player, input(), 1 / 60);
      if (player.fallSpeed > 0) impacts.push(player.fallSpeed);
    }
    expect(impacts).toHaveLength(1);
    expect(impacts[0]).toBeGreaterThan(FALL_SAFE_SPEED);
    expect(fallDamage(impacts[0])).toBeGreaterThan(0);
  });

  it('never reports an impact while resting', () => {
    const world = room();
    const player = grounded();
    for (let i = 0; i < 60; i++) {
      step(world, player, input(), 1 / 60);
      expect(player.fallSpeed).toBe(0);
    }
  });
});
