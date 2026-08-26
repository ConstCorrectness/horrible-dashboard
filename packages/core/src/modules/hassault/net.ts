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
import { applyImpulse, step, type MoveInput, type PlayerState } from './player';
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
  crouch: boolean;
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
  /**
   * Zoom step we were scoped to: 0 for none, 1-based into the weapon's
   * `zoomLevels`.
   *
   * Sent with the *shot* rather than as a state change of its own, because what
   * the server does with it is pick the cone for this trigger pull. It is
   * clamped against the weapon actually held — see `clamp_zoom` in
   * `backend/modules/hassault/weapons.py`.
   */
  scoped?: number;
  /**
   * Throw the grenade in `nade` on this frame.
   *
   * A flag on the movement command, exactly like `fire`, and for the same
   * reason: the throw has to carry the yaw, pitch and velocity of the frame it
   * left the hand on. A separate message would arrive with none of them and the
   * grenade would leave in a direction we were no longer looking.
   */
  throw?: boolean;
  /** Grenade slot to throw, or `-1`. */
  nade?: number;
  /** Underhand — a short throw, for putting a smoke at your own feet. */
  lob?: boolean;
}

/** The combat half of a command, decided by `ShotController` rather than by keys. */
export interface ShotIntent {
  fire: boolean;
  reload: boolean;
  weapon: number;
  viewT: number;
  /** Zoom step at the instant of the shot; 0 when not scoped. */
  scoped: number;
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
  /**
   * Crouch animation, 0 standing to 1 crouched.
   *
   * Public because it changes both what you see and what you can hit: the avatar
   * is drawn to this height and the server rewinds a shot against it. A crouching
   * enemy that still presents a standing hitbox is the kind of disagreement that
   * makes a game feel dishonest.
   */
  crouch: number;
}

/**
 * The movement state a client cannot derive from a snapshot on its own.
 *
 * Momentum made this necessary: with velocity in the simulation, rebasing on the
 * server's *position* alone and replaying leaves the replay running on the
 * client's own velocity, which is precisely the number that was wrong. So the
 * authoritative velocity rides in the private half of the envelope — it is
 * nobody else's business, and it would be sixteen extra numbers per packet in
 * the shared rows.
 *
 * `sinceLanded` is a **duration**, not a timestamp: the simulated clocks on the
 * two sides start whenever each side started and have no relation to each other,
 * so the only transferable form of "when did this body last land" is how long ago.
 */
export interface MoveState {
  vel: [number, number, number];
  /** Seconds airborne, which is what the gravity ramp reads. */
  air: number;
  crouch: number;
  crouchedInAir: boolean;
  /** Seconds since the last landing — the chain-boost window is measured from it. */
  sinceLanded: number;
}

/** One thing this player just heard. See `backend/modules/hassault/noise.py`. */
export interface NoiseEvent {
  /** `step`, `land`, `jump`, `shot`, `reload`, `hurt`, `die`. */
  kind: string;
  /** 0..1 after distance falloff and wall muffling. */
  volume: number;
  /** World bearing to the source, in radians. Deliberately **not** an offset:
   * a bearing and a loudness is what ears give you, and it is all the wire says. */
  bearing: number;
  /** -1 below, 0 level, 1 above. */
  up: number;
  /** Which weapon made it — shots only, absent on every other kind. Enough to
   * tell a sniper round from a shotgun blast two rooms away, which is a real
   * decision; not enough to locate either. */
  weapon?: string;
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
  /** What prediction rebases on. Absent only from a server older than momentum. */
  move?: MoveState;
  /** Audible noises since the last snapshot, drained server-side. */
  noise?: NoiseEvent[];
  /** Health lost to the last landing, so the HUD can say why. */
  fell?: number;
  /** What we are carrying, keyed by grenade id. Private, like `ammo`. */
  nades?: Record<string, number>;
  /**
   * How blind a flashbang has left *us*, 0..1.
   *
   * Resolved per player on the server, because it depends on where we were
   * looking and whether a wall was in the way — see `grenades.flash_strength`.
   * A client that computed its own would make not being blinded a setting.
   */
  flash?: number;
  /**
   * Enemy ids our team can currently see, for the radar.
   *
   * Teammates are deliberately *not* in this list: they are always shown, so
   * saying so every tick would be a per-player id list that never changes.
   */
  spotted?: string[];
}

/** A grenade in the air. Public — it is a thing on everybody's screen. */
export interface NadeRow {
  id: string;
  /** `he` | `flash` | `smoke` | `fire`. */
  kind: string;
  owner: string;
  team: number;
  x: number;
  y: number;
  z: number;
  /** Seconds of fuse left, for the tick that gets louder as it runs out. */
  fuse: number;
}

/** A smoke cloud or a patch of fire: an effect that persists in a place. */
export interface ZoneRow {
  id: string;
  /** `smoke` | `fire`. */
  kind: string;
  x: number;
  y: number;
  z: number;
  r: number;
  /** Seconds left, so a cloud can thin as it dies rather than vanishing. */
  left: number;
  duration: number;
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

/** A grenade going off. The client turns this into light, sound and debris. */
export interface DetonateFx {
  kind: 'detonate';
  /** `he` | `flash` | `smoke` | `fire`. */
  nade: string;
  id: string;
  at: [number, number, number];
  radius: number;
}

export type Fx = ShotFx | KillFx | SpawnFx | DetonateFx;

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
  /** Grenades in the air. Public, unlike the noise envelope. */
  nades?: NadeRow[];
  /** Smoke and fire currently standing in the world. */
  zones?: ZoneRow[];
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
  /**
   * Weapon kickback by command sequence.
   *
   * Kept beside the pending list rather than on the `Command` because it must
   * **not** go on the wire: the server derives the shooter's push from its own
   * weapon table, and a client-supplied impulse would be a client-supplied
   * velocity. A replay still has to apply it, though — it is part of what moved
   * us — so it is remembered here and dropped on the same ack.
   */
  private kicks = new Map<number, Vec3>();
  /** Visual-only offset, decayed to zero, so corrections do not jolt the camera. */
  correction: Vec3 = { x: 0, y: 0, z: 0 };
  /** Last correction magnitude, for the HUD. */
  lastError = 0;

  /**
   * Predict `input` locally and record it for replay. Returns the command so the
   * caller can queue it for the next send.
   *
   * `kick` is the recoil push a shot on this frame produces, applied *after* the
   * step — which is exactly where the server applies it (`simulate` steps, then
   * `_handle_combat` fires). Getting that order wrong mispredicts every shot.
   */
  record(
    world: World,
    player: PlayerState,
    input: MoveInput,
    dt: number,
    shot?: ShotIntent,
    kick?: Vec3,
    thrown?: { throw: boolean; nade: number; lob: boolean },
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
      crouch: input.crouch,
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
        // Only on a shot, and only when actually scoped: it decides this pull's
        // cone and nothing else, so on every other frame it would be a number
        // the server reads and discards sixty times a second.
        if (shot.scoped > 0) command.scoped = shot.scoped;
      }
      if (shot.reload) command.reload = true;
      if (shot.weapon >= 0) command.weapon = shot.weapon;
    }
    // Same rule as the combat fields: only on the frame it happened. A throw is
    // an edge, so this is set on exactly one command however long the key is
    // held — see `GrenadeController` in `utility.ts`.
    if (thrown?.throw && thrown.nade >= 0) {
      command.throw = true;
      command.nade = thrown.nade;
      if (thrown.lob) command.lob = true;
    }
    this.pending.push(command);
    step(world, player, input, clamped);
    if (kick && (kick.x !== 0 || kick.y !== 0 || kick.z !== 0)) {
      this.kicks.set(command.seq, { ...kick });
      applyImpulse(player, kick.x, kick.y, kick.z);
    }
    return command;
  }

  /**
   * Rebase the local player on an authoritative state and replay what the server
   * has not seen yet.
   *
   * `authoritative` is where the server had us as of `ack`; the commands after it
   * are ours alone and still stand.
   */
  reconcile(
    world: World,
    player: PlayerState,
    authoritative: PlayerRow,
    move: MoveState | null,
    ack: number,
  ): void {
    this.pending = this.pending.filter((c) => c.seq > ack);
    for (const seq of [...this.kicks.keys()]) if (seq <= ack) this.kicks.delete(seq);

    const predictedX = player.x;
    const predictedY = player.y;
    const predictedZ = player.z;

    player.x = authoritative.x;
    player.y = authoritative.y;
    player.z = authoritative.z;
    player.onGround = authoritative.ground;
    // Momentum has to be rebased too. Replaying on top of the client's own
    // velocity would run the replay on the very number the correction exists to
    // fix, and the error would then compound rather than settle. A server too old
    // to send it leaves the predicted velocity alone, which is the best available
    // guess rather than a lie.
    if (move) {
      player.velX = move.vel[0];
      player.velY = move.vel[1];
      player.velZ = move.vel[2];
      player.timeInAir = move.air;
      player.crouch = move.crouch;
      player.crouchedInAir = move.crouchedInAir;
      // A duration, converted against *our* simulated clock — the two clocks are
      // unrelated, so the timestamp itself would be meaningless here.
      player.landedAt = player.t - move.sinceLanded;
    }
    for (const command of this.pending) {
      // Replay uses each command's *recorded* view angles rather than the
      // player's current ones, or turning mid-correction bends the whole
      // replayed path.
      player.yaw = command.yaw;
      player.pitch = command.pitch;
      step(
        world,
        player,
        {
          forward: command.forward,
          strafe: command.strafe,
          jump: command.jump,
          crouch: command.crouch,
          noclip: false,
        },
        command.dt,
      );
      // Recoil is part of what moved us, so a replay that skips it lands short
      // of where we already drew ourselves.
      const kick = this.kicks.get(command.seq);
      if (kick) applyImpulse(player, kick.x, kick.y, kick.z);
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
    this.kicks.clear();
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

  /**
   * The newest packet, un-interpolated.
   *
   * For the things that are *not* players: grenades and zones have no
   * prediction to reconcile against and no correction to fight, so the newest
   * server truth is simply correct — and their renderer does its own smoothing
   * toward it (`NadePool`). Sampling them through the interpolation buffer would
   * hold a cloud 100 ms in the past for no benefit.
   */
  get latest(): Snapshot | null {
    return this.snapshots.length > 0 ? this.snapshots[this.snapshots.length - 1] : null;
  }

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
