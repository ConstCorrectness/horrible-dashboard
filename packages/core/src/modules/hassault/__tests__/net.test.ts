/**
 * Client netcode: prediction, reconciliation, and interpolation.
 *
 * These import `net.ts` directly and never `session.ts` — the latter reaches the
 * shell's socket module, which opens a real WebSocket at import time and has no
 * business in a node test run.
 */
import { describe, expect, it } from 'vitest';

import type { MapInfo } from '../api';
import {
  INTERP_DELAY_MS,
  lerpAngle,
  PingTracker,
  Predictor,
  SNAP_DISTANCE,
  SnapshotBuffer,
  type MoveState,
  type PlayerRow,
  type Snapshot,
} from '../net';
import { createPlayer, step, type PlayerState } from '../player';
import { SOLID, SPACE, World } from '../world';

const PLANES = ['type', 'floor', 'ceil', 'wtex', 'ftex', 'ctex', 'vdelta', 'utex', 'tag'];

/** An open room with a solid border, big enough for the 2.2-cube body. */
function room(ssize = 32): World {
  const n = ssize * ssize;
  const buf = new ArrayBuffer(n * PLANES.length);
  const at = (name: string) => {
    const off = PLANES.indexOf(name) * n;
    return name === 'floor' || name === 'ceil'
      ? new Int8Array(buf, off, n)
      : new Uint8Array(buf, off, n);
  };
  const type = at('type');
  const ceil = at('ceil');
  type.fill(SOLID);
  ceil.fill(16);
  for (let y = 2; y < ssize - 2; y++) {
    for (let x = 2; x < ssize - 2; x++) type[y * ssize + x] = SPACE;
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

function rowFrom(id: string, p: PlayerState, over: Partial<PlayerRow> = {}): PlayerRow {
  return {
    id,
    name: id,
    team: 0,
    x: p.x,
    y: p.y,
    z: p.z,
    yaw: p.yaw,
    pitch: p.pitch,
    ground: p.onGround,
    stale: false,
    rtt: 0,
    hp: 100,
    alive: true,
    weapon: 2,
    kills: 0,
    deaths: 0,
    bot: false,
    crouch: 0,
    ...over,
  };
}

/**
 * The private movement block a snapshot carries alongside the public row.
 *
 * Reconciliation needs it: with momentum in the simulation, rebasing on position
 * alone and replaying would run the replay on the client's own velocity — the very
 * number the correction exists to fix.
 */
function moveFrom(p: PlayerState): MoveState {
  return {
    vel: [p.velX, p.velY, p.velZ],
    air: p.timeInAir,
    crouch: p.crouch,
    crouchedInAir: p.crouchedInAir,
    sinceLanded: Math.max(0, p.t - p.landedAt),
  };
}

function snapshot(t: number, ack: number, players: PlayerRow[]): Snapshot {
  return { room: 'r', tick: t, t, ack, players };
}

describe('lerpAngle', () => {
  it('takes the short way across the wrap point', () => {
    // From just under +π to just over -π is a small step, not most of a circle.
    const a = Math.PI - 0.1;
    const b = -Math.PI + 0.1;
    const mid = lerpAngle(a, b, 0.5);
    // The midpoint sits at the wrap, not back near zero.
    expect(Math.abs(Math.abs(mid) - Math.PI)).toBeLessThan(0.01);
  });

  it('interpolates normally away from the wrap', () => {
    expect(lerpAngle(0, 1, 0.5)).toBeCloseTo(0.5, 6);
  });
});

describe('Predictor', () => {
  it('numbers commands from one, increasing', () => {
    const world = room();
    const predictor = new Predictor();
    const player = createPlayer(16, 16, 0);
    const a = predictor.record(
      world,
      player,
      { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false },
      1 / 60,
    );
    const b = predictor.record(
      world,
      player,
      { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false },
      1 / 60,
    );
    expect(a.seq).toBe(1);
    expect(b.seq).toBe(2);
    expect(predictor.unacked()).toHaveLength(2);
  });

  it('moves the player immediately rather than waiting for the server', () => {
    const world = room();
    const predictor = new Predictor();
    const player = createPlayer(16, 16, 0);
    predictor.record(
      world,
      player,
      { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false },
      1 / 60,
    );
    expect(player.x).toBeGreaterThan(16);
  });

  it('clamps a huge frame the same way the server does', () => {
    const world = room();
    const predictor = new Predictor();
    const player = createPlayer(16, 16, 0);
    const command = predictor.record(
      world,
      player,
      { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false },
      5,
    );
    // Recording the unclamped value would make the replay step further than the
    // server ever simulated, and the two would never agree again.
    expect(command.dt).toBeCloseTo(0.1, 10);
  });

  it('reconciles to zero error when the server agrees', () => {
    const world = room();
    const predictor = new Predictor();
    const player = createPlayer(16, 16, 0);
    // The server, simulating the same commands from the same start.
    const authoritative = createPlayer(16, 16, 0);

    const commands = [];
    for (let i = 0; i < 10; i++) {
      commands.push(
        predictor.record(
          world,
          player,
          { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false },
          1 / 60,
        ),
      );
    }
    for (const c of commands) {
      authoritative.yaw = c.yaw;
      step(
        world,
        authoritative,
        { forward: c.forward, strafe: c.strafe, jump: c.jump, crouch: false, noclip: false },
        c.dt,
      );
    }

    const before = { x: player.x, y: player.y, z: player.z };
    predictor.reconcile(world, player, rowFrom('me', authoritative), moveFrom(authoritative), 10);
    expect(predictor.unacked()).toHaveLength(0);
    expect(predictor.lastError).toBeLessThan(1e-9);
    expect(player.x).toBeCloseTo(before.x, 9);
  });

  it('replays only the commands the server has not acknowledged', () => {
    const world = room();
    const predictor = new Predictor();
    const player = createPlayer(16, 16, 0);
    const authoritative = createPlayer(16, 16, 0);

    const commands = [];
    for (let i = 0; i < 10; i++) {
      commands.push(
        predictor.record(
          world,
          player,
          { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false },
          1 / 60,
        ),
      );
    }
    // The server has only seen the first six.
    for (const c of commands.slice(0, 6)) {
      authoritative.yaw = c.yaw;
      step(
        world,
        authoritative,
        { forward: c.forward, strafe: c.strafe, jump: c.jump, crouch: false, noclip: false },
        c.dt,
      );
    }

    const predicted = player.x;
    predictor.reconcile(world, player, rowFrom('me', authoritative), moveFrom(authoritative), 6);
    expect(predictor.unacked()).toHaveLength(4);
    // The four unacknowledged commands are ours and still stand, so we end up
    // exactly where we already were.
    expect(player.x).toBeCloseTo(predicted, 9);
    expect(predictor.lastError).toBeLessThan(1e-9);
  });

  it('smooths a small disagreement instead of snapping', () => {
    const world = room();
    const predictor = new Predictor();
    const player = createPlayer(16, 16, 0);
    predictor.record(
      world,
      player,
      { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false },
      1 / 60,
    );

    const server = createPlayer(player.x - 0.5, player.y, player.z);
    predictor.reconcile(world, player, rowFrom('me', server), moveFrom(server), 1);
    expect(predictor.lastError).toBeCloseTo(0.5, 6);
    // The simulation moves to the truth; the camera offset hides the jump.
    expect(player.x).toBeCloseTo(server.x, 9);
    expect(predictor.correction.x).toBeCloseTo(0.5, 6);
  });

  it('snaps rather than easing when the disagreement is large', () => {
    const world = room();
    const predictor = new Predictor();
    const player = createPlayer(16, 16, 0);
    predictor.record(
      world,
      player,
      { forward: 1, strafe: 0, jump: false, crouch: false, noclip: false },
      1 / 60,
    );

    const server = createPlayer(player.x - (SNAP_DISTANCE + 1), player.y, player.z);
    predictor.reconcile(world, player, rowFrom('me', server), moveFrom(server), 1);
    // Easing across that distance would draw the player somewhere they are not
    // for the whole ease — possibly through a wall.
    expect(predictor.correction).toEqual({ x: 0, y: 0, z: 0 });
    expect(player.x).toBeCloseTo(server.x, 9);
  });

  it('decays the visual correction towards zero', () => {
    const predictor = new Predictor();
    predictor.correction = { x: 1, y: 1, z: 1 };
    predictor.decay(0.05);
    expect(predictor.correction.x).toBeLessThan(1);
    expect(predictor.correction.x).toBeGreaterThan(0);
    predictor.decay(1);
    expect(Math.abs(predictor.correction.x)).toBeLessThan(0.01);
  });

  it('keeps sequence numbers monotonic across a reset', () => {
    // A rejoin must not reuse sequence numbers a server might still be holding.
    const world = room();
    const predictor = new Predictor();
    const player = createPlayer(16, 16, 0);
    predictor.record(
      world,
      player,
      { forward: 0, strafe: 0, jump: false, crouch: false, noclip: false },
      1 / 60,
    );
    predictor.reset();
    const next = predictor.record(
      world,
      player,
      { forward: 0, strafe: 0, jump: false, crouch: false, noclip: false },
      1 / 60,
    );
    expect(next.seq).toBe(2);
  });
});

describe('SnapshotBuffer', () => {
  const other = (x: number, yaw = 0): PlayerRow => ({
    id: 'other',
    name: 'other',
    team: 1,
    x,
    y: 8,
    z: 0,
    yaw,
    pitch: 0,
    ground: true,
    stale: false,
    rtt: 0,
    hp: 100,
    alive: true,
    weapon: 2,
    kills: 0,
    deaths: 0,
    bot: false,
    crouch: 0,
  });

  it('interpolates between the two snapshots straddling the render time', () => {
    const buffer = new SnapshotBuffer();
    // Local clock equals server clock here, so render time is exactly
    // `now - INTERP_DELAY_MS`.
    buffer.push(snapshot(1000, 0, [other(10)]), 1000);
    buffer.push(snapshot(1100, 0, [other(20)]), 1100);
    // Render time 1050: halfway.
    const rows = buffer.sample(1050 + INTERP_DELAY_MS, 'me');
    expect(rows).toHaveLength(1);
    expect(rows[0].x).toBeCloseTo(15, 6);
  });

  it('holds the last known position rather than extrapolating past it', () => {
    const buffer = new SnapshotBuffer();
    buffer.push(snapshot(1000, 0, [other(10)]), 1000);
    buffer.push(snapshot(1100, 0, [other(20)]), 1100);
    // Far beyond the newest snapshot. A guess here walks people through walls.
    const rows = buffer.sample(5000 + INTERP_DELAY_MS, 'me');
    expect(rows[0].x).toBeCloseTo(20, 6);
  });

  it('excludes our own player, who comes from prediction', () => {
    const buffer = new SnapshotBuffer();
    const me: PlayerRow = { ...other(1), id: 'me', name: 'me' };
    buffer.push(snapshot(1000, 0, [me, other(10)]), 1000);
    buffer.push(snapshot(1100, 0, [me, other(20)]), 1100);
    const rows = buffer.sample(1050 + INTERP_DELAY_MS, 'me');
    expect(rows.map((r) => r.id)).toEqual(['other']);
  });

  it('interpolates yaw the short way round', () => {
    const buffer = new SnapshotBuffer();
    buffer.push(snapshot(1000, 0, [other(10, Math.PI - 0.1)]), 1000);
    buffer.push(snapshot(1100, 0, [other(10, -Math.PI + 0.1)]), 1100);
    const rows = buffer.sample(1050 + INTERP_DELAY_MS, 'me');
    expect(Math.abs(Math.abs(rows[0].yaw) - Math.PI)).toBeLessThan(0.01);
  });

  it('renders in the past by the interpolation delay', () => {
    const buffer = new SnapshotBuffer();
    buffer.push(snapshot(1000, 0, [other(10)]), 1000);
    expect(buffer.renderTime(2000)).toBeCloseTo(2000 - INTERP_DELAY_MS, 6);
  });

  it('estimates the clock offset from the least-delayed sample', () => {
    const buffer = new SnapshotBuffer();
    // Server clock is 5000 ahead; the second sample was queued 80 ms.
    buffer.push(snapshot(5000, 0, [other(10)]), 0);
    buffer.push(snapshot(5100, 0, [other(20)]), 180);
    // The minimum offset (-5000) is the honest one, so render time tracks it.
    expect(buffer.renderTime(200)).toBeCloseTo(5200 - INTERP_DELAY_MS, 6);
  });

  it('trims history but always keeps enough to interpolate', () => {
    const buffer = new SnapshotBuffer();
    for (let i = 0; i < 200; i++)
      buffer.push(snapshot(1000 + i * 50, 0, [other(i)]), 1000 + i * 50);
    expect(buffer.size).toBeLessThan(200);
    expect(buffer.size).toBeGreaterThanOrEqual(2);
  });

  it('returns nothing before the first snapshot arrives', () => {
    expect(new SnapshotBuffer().sample(1000, 'me')).toEqual([]);
  });
});

describe('PingTracker', () => {
  it('reports the median so one stall does not define the reading', () => {
    const ping = new PingTracker();
    [20, 22, 21, 900, 19].forEach((v) => ping.record(v));
    expect(ping.rtt).toBeLessThan(100);
  });

  it('reads zero before any sample', () => {
    expect(new PingTracker().rtt).toBe(0);
  });
});
