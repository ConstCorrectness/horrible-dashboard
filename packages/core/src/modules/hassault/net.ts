/**
 * Client netcode: prediction, reconciliation, and remote-player interpolation.
 *
 * The server is authoritative (`backend/modules/hassault/match.py`), but waiting
 * for it would put a full round trip between pressing W and moving. So the client
 * simulates its own player immediately and corrects afterwards:
 *
 * 1. Each frame produces a **command** with a sequence number, which is applied
 *    locally at once and kept in a pending list.
 * 2. Snapshots carry `ack` — the last command the server consumed from us. Every
 *    pending command at or below it is confirmed and dropped.
 * 3. The remaining commands are **replayed** on top of the authoritative state.
 *    If our prediction was right the replay lands exactly where we already are
 *    and nothing moves.
 *
 * Remote players get the opposite treatment: they are rendered ~100 ms in the
 * past, between two snapshots that have both already arrived, so ordinary jitter
 * never becomes visible motion.
 *
 * Kept free of three and of React so all of it is unit-testable headless.
 */
import { step, type MoveInput, type PlayerState } from './player';
import type { World } from './world';

/**
 * How far behind the newest snapshot remote players are drawn.
 *
 * Two snapshot intervals at 20 Hz. Less than one interval and there is routinely
 * no later snapshot to interpolate towards, which is exactly when a renderer
 * starts extrapolating and walking people through walls.
 */
export const INTERP_DELAY_MS = 100;

/** Snapshot history kept. Enough to ride out a stall without unbounded growth. */
export const SNAPSHOT_BUFFER_MS = 2000;

/**
 * Position error beyond which the camera is snapped rather than eased.
 *
 * Small errors are smoothed because a constant micro-jitter reads as broken;
 * large ones are not, because easing across a wall shows the player somewhere
 * they are not, for as long as the ease lasts.
 */
export const SNAP_DISTANCE = 2.0;

/** Per-second decay of the visual error offset. 0.001 ≈ gone in ~150 ms. */
const CORRECTION_DECAY = 0.001;

export interface Command {
  seq: number;
  forward: number;
  strafe: number;
  jump: boolean;
  yaw: number;
  pitch: number;
  dt: number;
  fire?: boolean;
  reload?: boolean;
  /** Slot to switch to, or `-1` for no change. */
  weapon?: number;
  /**
   * Server-clock ms this frame was *rendering* — `SnapshotBuffer.renderTime`.
   *
   * The server rewinds a shot to this instant, so it is what makes hitting a
   * moving target possible without leading them by a body width. It is clamped
   * server-side; see `backend/modules/hassault/weapons.py`.
   */
  viewT?: number;
}

/** The combat half of a command, decided by `ShotController` rather than by keys. */
export interface ShotIntent {
  fire: boolean;
  reload: boolean;
  weapon: number;
  viewT: number;
}

export interface PlayerRow {
  id: string;
  name: string;
  team: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  pitch: number;
  ground: boolean;
  stale: boolean;
  rtt: number;
  /** Public: a wounded enemy is what makes a firefight a decision. */
  hp: number;
  alive: boolean;
  weapon: number;
  kills: number;
  deaths: number;
  bot: boolean;
}

/** The half of our own state nobody else is sent. */
export interface SelfState {
  hp: number;
  alive: boolean;
  weapon: number;
  ammo: number;
  /** `-1` is unlimited. */
  reserve: number;
  reloading: boolean;
  reloadIn: number;
  respawnIn: number;
  protected: boolean;
  kills: number;
  deaths: number;
  mag: number;
  /** Hitmarkers since the last snapshot. Drained server-side, so each is sent once. */
  hits: { victim: string; damage: number; head: boolean; killed: boolean }[];
}

/** A shot somebody took, batched into the snapshot rather than sent as it happened. */
export interface ShotFx {
  kind: 'shot';
  id: string;
  weapon: number;
  origin: [number, number, number];
  /** One endpoint per pellet — a wall, a body, or the end of its range. */
  ends: [number, number, number][];
  hit: boolean;
}

export interface KillFx {
  kind: 'kill';
  victim: string;
  victimName: string;
  killer: string;
  killerName: string;
  weapon: string;
  head: boolean;
}

export interface SpawnFx {
  kind: 'spawn';
  id: string;
}

export type Fx = ShotFx | KillFx | SpawnFx;

export interface Snapshot {
  room: string;
  tick: number;
  /** Server clock in ms. */
  t: number;
  ack: number;
  players: PlayerRow[];
  you?: SelfState;
  scores?: number[];
  fx?: Fx[];
}

/** A three-component offset, in cube units. */
export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

/**
 * Interpolate an angle the short way round.
 *
 * A player turning past π would otherwise spin most of a full circle the wrong
 * way between two snapshots — the one interpolation bug everybody writes once.
 */
export function lerpAngle(a: number, b: number, t: number): number {
  let diff = (b - a) % (Math.PI * 2);
  if (diff > Math.PI) diff -= Math.PI * 2;
  if (diff < -Math.PI) diff += Math.PI * 2;
  return a + diff * t;
}

/**
 * Local prediction and its correction against the server.
 *
 * Owns the pending-command list and the sequence counter; the caller owns the
 * `PlayerState` so the render loop can keep using it directly.
 */
export class Predictor {
  private seq = 0;
  private pending: Command[] = [];
  /** Visual-only offset, decayed to zero, so corrections do not jolt the camera. */
  correction: Vec3 = { x: 0, y: 0, z: 0 };
  /** Last correction magnitude, for the HUD. */
  lastError = 0;

  /**
   * Predict `input` locally and record it for replay. Returns the command so the
   * caller can queue it for the next send.
   */
  record(
    world: World,
    player: PlayerState,
    input: MoveInput,
    dt: number,
    shot?: ShotIntent,
  ): Command {
    this.seq += 1;
    // The server clamps dt the same way; recording the unclamped value would
    // make the client replay a step the server never simulated.
    const clamped = Math.min(Math.max(dt, 0), 0.1);
    const command: Command = {
      seq: this.seq,
      forward: input.forward,
      strafe: input.strafe,
      jump: input.jump,
      yaw: player.yaw,
      pitch: player.pitch,
      dt: clamped,
    };
    // Only when there is something to say. Combat fields ride on movement
    // commands so a shot carries the exact angles of the frame it happened on,
    // but most frames are not shots and an empty field is bytes on the wire
    // sixty times a second.
    if (shot && (shot.fire || shot.reload || shot.weapon >= 0)) {
      if (shot.fire) {
        command.fire = true;
        command.viewT = shot.viewT;
      }
      if (shot.reload) command.reload = true;
      if (shot.weapon >= 0) command.weapon = shot.weapon;
    }
    this.pending.push(command);
    step(world, player, input, clamped);
    return command;
  }

  /**
   * Rebase the local player on an authoritative state and replay what the server
   * has not seen yet.
   *
   * `authoritative` is where the server had us as of `ack`; the commands after it
   * are ours alone and still stand.
   */
  reconcile(world: World, player: PlayerState, authoritative: PlayerRow, ack: number): void {
    this.pending = this.pending.filter((c) => c.seq > ack);

    const predictedX = player.x;
    const predictedY = player.y;
    const predictedZ = player.z;

    player.x = authoritative.x;
    player.y = authoritative.y;
    player.z = authoritative.z;
    // Velocity and ground state are not on the wire: they are derivable, and a
    // snapshot carrying them would still be a snapshot behind. Replay rebuilds
    // them, and with an empty pending list the first local frame does.
    for (const command of this.pending) {
      // Replay uses each command's *recorded* view angles rather than the
      // player's current ones, or turning mid-correction bends the whole
      // replayed path.
      player.yaw = command.yaw;
      player.pitch = command.pitch;
      step(
        world,
        player,
        { forward: command.forward, strafe: command.strafe, jump: command.jump, noclip: false },
        command.dt,
      );
    }
    // Restore the live view angles: the camera must follow the mouse, not the
    // last command the server happens to have acknowledged.
    const last = this.pending[this.pending.length - 1];
    if (last) {
      player.yaw = last.yaw;
      player.pitch = last.pitch;
    }

    const dx = predictedX - player.x;
    const dy = predictedY - player.y;
    const dz = predictedZ - player.z;
    const error = Math.hypot(dx, dy, dz);
    this.lastError = error;
    if (error > SNAP_DISTANCE) {
      // Too far to hide. Show the truth immediately.
      this.correction = { x: 0, y: 0, z: 0 };
    } else {
      // Keep drawing where we were, and walk that offset to zero.
      this.correction = { x: dx, y: dy, z: dz };
    }
  }

  /** Decay the visual correction. Call once per rendered frame. */
  decay(dt: number): void {
    const k = Math.pow(CORRECTION_DECAY, dt);
    this.correction.x *= k;
    this.correction.y *= k;
    this.correction.z *= k;
  }

  /** Commands the server has not acknowledged, oldest first. */
  unacked(): Command[] {
    return this.pending;
  }

  reset(): void {
    this.pending = [];
    this.correction = { x: 0, y: 0, z: 0 };
    this.lastError = 0;
  }
}

/**
 * Snapshot history for everyone who is not us, sampled in the past.
 *
 * Server and client clocks are unrelated, so rather than trying to synchronise
 * them this tracks the smallest `localArrival - serverTimestamp` ever seen. The
 * minimum is the sample with the least queuing delay, which is the best estimate
 * of the true offset available without a full clock-sync protocol — and it is
 * stable, which matters more here than being exactly right.
 */
export class SnapshotBuffer {
  private snapshots: Snapshot[] = [];
  private offset = Number.POSITIVE_INFINITY;

  push(snapshot: Snapshot, localNow: number): void {
    this.offset = Math.min(this.offset, localNow - snapshot.t);
    this.snapshots.push(snapshot);
    this.snapshots.sort((a, b) => a.t - b.t);
    const cutoff = snapshot.t - SNAPSHOT_BUFFER_MS;
    while (this.snapshots.length > 2 && this.snapshots[0].t < cutoff) this.snapshots.shift();
  }

  /** Server-clock time the renderer should be showing right now. */
  renderTime(localNow: number): number {
    return localNow - this.offset - INTERP_DELAY_MS;
  }

  /**
   * Every remote player's position at `localNow`, interpolated.
   *
   * `selfId` is excluded because our own player comes from prediction — drawing
   * the interpolated copy too would render us 100 ms behind ourselves.
   */
  sample(localNow: number, selfId: string): PlayerRow[] {
    if (this.snapshots.length === 0) return [];
    const target = this.renderTime(localNow);

    let older = this.snapshots[0];
    let newer: Snapshot | null = null;
    for (const snapshot of this.snapshots) {
      if (snapshot.t <= target) older = snapshot;
      else {
        newer = snapshot;
        break;
      }
    }

    // Past the newest snapshot: hold the last known position rather than
    // extrapolate. A brief stall is honest; a guess walks people through walls.
    if (!newer) return older.players.filter((p) => p.id !== selfId);

    const span = newer.t - older.t;
    const t = span > 0 ? Math.min(1, Math.max(0, (target - older.t) / span)) : 0;
    const byId = new Map(newer.players.map((p) => [p.id, p]));
    return older.players
      .filter((p) => p.id !== selfId)
      .map((from) => {
        const to = byId.get(from.id);
        // Someone present in the older snapshot but not the newer has left;
        // holding their last position is right for this frame — the `left` event
        // is what removes them.
        if (!to) return from;
        return {
          ...to,
          x: from.x + (to.x - from.x) * t,
          y: from.y + (to.y - from.y) * t,
          z: from.z + (to.z - from.z) * t,
          yaw: lerpAngle(from.yaw, to.yaw, t),
          pitch: from.pitch + (to.pitch - from.pitch) * t,
        };
      });
  }

  clear(): void {
    this.snapshots = [];
    this.offset = Number.POSITIVE_INFINITY;
  }

  get size(): number {
    return this.snapshots.length;
  }
}

/** Round-trip time, as a median of recent samples. */
export class PingTracker {
  private samples: number[] = [];

  record(rttMs: number): void {
    this.samples.push(rttMs);
    if (this.samples.length > 8) this.samples.shift();
  }

  /** Median, not mean: one stalled reply should not define the reading. */
  get rtt(): number {
    if (this.samples.length === 0) return 0;
    const sorted = [...this.samples].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
  }

  reset(): void {
    this.samples = [];
  }
}
