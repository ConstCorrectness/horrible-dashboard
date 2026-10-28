/**
 * HorribleAssault Match Recording & Cinematic Replay Suite (.hademo)
 *
 * Provides:
 * 1. HADEMO demo recording format & serialization (tick-based player states, shots, kills, streaks).
 * 2. Playback controller with variable speed (0.1x slow-mo to 4.0x fast-forward), seeking, and frag bookmarks.
 * 3. 6-DOF Cinematic Drone Freecam with camera roll (Q/E), FOV zoom lens (15° to 120°), and inertia smoothing.
 * 4. Catmull-Rom spline dolly keyframe interpolation for montage cinematic camera sweeps.
 * 5. Clean view toggle (Ctrl+H) for zero-HUD video capture.
 *
 * The panel feeds the recorder once per frame (`due` → `recordFrame`, the
 * player's own view plus every body the avatar pool drew) and, while a demo
 * plays, draws `sampleCurrentFrame` through the same pool. Neither half was
 * wired at first: every saved demo had `"frames": []`, and loading one showed a
 * bar over a world nothing moved in.
 *
 * Angles in a frame are the wire's: radians, yaw 0 = +x (`PlayerRow`). The
 * freecam keeps its own degrees, yaw 0 = +y; `freecamFromView` converts.
 */
import type { PlayerRow } from './net';

export interface DemoHeader {
  magic: 'HADEMO';
  version: 1;
  map: string;
  recordedAt: number;
  /** Frames per second the recorder keeps; playback interpolates between them. */
  tickRate: number;
  playerNames: Record<string, string>;
  durationMs: number;
  /** The recorder's own id in `frames`: whose eyes playback starts behind. */
  pov?: string;
}

export interface DemoPlayerSnapshot {
  /** The wire's player id. Numbers in a hand-written file are read as strings. */
  id: string;
  name: string;
  team: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  pitch: number;
  roll?: number;
  weapon: number;
  hp: number;
  crouching?: boolean;
  sprinting?: boolean;
  sliding?: boolean;
  /** Crouch animation, 0 standing to 1 crouched (`PlayerRow.crouch`). */
  crouch?: number;
  alive?: boolean;
}

export interface DemoEvent {
  tick: number;
  timestamp: number;
  type: 'kill' | 'shot' | 'hit' | 'grenade' | 'streak';
  killerId?: string;
  victimId?: string;
  killer?: string;
  victim?: string;
  weapon?: string;
  head?: boolean;
  nutshot?: boolean;
  streak?: number;
  origin?: [number, number, number];
  target?: [number, number, number];
}

export interface DemoBookmark {
  tick: number;
  timestamp: number;
  label: string;
  type: 'kill' | 'streak' | 'nutshot' | 'headshot';
  targetPlayerId?: string;
}

export interface DemoFrame {
  tick: number;
  timestamp: number;
  players: DemoPlayerSnapshot[];
  events: DemoEvent[];
}

export interface DemoData {
  header: DemoHeader;
  frames: DemoFrame[];
  bookmarks: DemoBookmark[];
}

export interface DollyKeyframe {
  time: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  pitch: number;
  roll: number;
  fov: number;
}

/** Catmull-Rom cubic interpolation between 4 points */
export function catmullRom(p0: number, p1: number, p2: number, p3: number, t: number): number {
  const t2 = t * t;
  const t3 = t2 * t;
  return (
    0.5 *
    (2 * p1 +
      (-p0 + p2) * t +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 +
      (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
  );
}

/** Interpolates angle without 360-degree wrap-around artifacts */
export function interpolateAngle(a0: number, a1: number, t: number): number {
  let diff = (a1 - a0) % 360;
  if (diff > 180) diff -= 360;
  if (diff < -180) diff += 360;
  return a0 + diff * t;
}

/** As `interpolateAngle`, in radians: the shortest way round. */
export function interpolateRadians(a0: number, a1: number, t: number): number {
  const turn = Math.PI * 2;
  let diff = (a1 - a0) % turn;
  if (diff > Math.PI) diff -= turn;
  if (diff < -Math.PI) diff += turn;
  return a0 + diff * t;
}

/**
 * The freecam's angles for a view given the wire's way (radians, yaw 0 = +x),
 * so switching to it starts exactly where the camera already is. The freecam's
 * forward is `(-sin a, cos a)` and the player's `(cos y, sin y)`: `a = y - 90°`.
 */
export function freecamFromView(yaw: number, pitch: number): { yaw: number; pitch: number } {
  return { yaw: ((yaw - Math.PI / 2) * 180) / Math.PI, pitch: (pitch * 180) / Math.PI };
}

/** The freecam's view back in the wire's terms, for the camera code. */
export function viewFromFreecam(cam: DroneFreecam): { yaw: number; pitch: number; roll: number } {
  return {
    yaw: (cam.yaw * Math.PI) / 180 + Math.PI / 2,
    pitch: (cam.pitch * Math.PI) / 180,
    roll: (cam.roll * Math.PI) / 180,
  };
}

/** A body the avatar pool drew, as a frame keeps it. */
export function snapshotFromRow(row: PlayerRow): DemoPlayerSnapshot {
  return {
    id: row.id,
    name: row.name,
    team: row.team,
    x: row.x,
    y: row.y,
    z: row.z,
    yaw: row.yaw,
    pitch: row.pitch,
    weapon: row.weapon,
    hp: row.hp,
    crouch: row.crouch,
    alive: row.alive,
  };
}

/** A frame's body, back in the shape the avatar pool draws. */
export function rowFromSnapshot(s: DemoPlayerSnapshot): PlayerRow {
  return {
    id: s.id,
    name: s.name,
    team: s.team,
    x: s.x,
    y: s.y,
    z: s.z,
    yaw: s.yaw,
    pitch: s.pitch,
    ground: true,
    stale: false,
    rtt: 0,
    hp: s.hp,
    alive: s.alive ?? s.hp > 0,
    weapon: s.weapon,
    kills: 0,
    deaths: 0,
    bot: false,
    crouch: s.crouch ?? (s.crouching ? 1 : 0),
  };
}

/** Why a file is not a demo this build can play, in words for the player. */
export class DemoError extends Error {}

/**
 * A `.hademo` file's text, checked and normalised: ids as strings, and refused
 * with a reason rather than loaded into a player that will draw nothing. An
 * empty recording is the case that matters — it is what every demo saved before
 * the recorder was wired contains.
 */
export function parseDemo(text: string): DemoData {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new DemoError('This file is not a demo: it is not JSON.');
  }
  const data = raw as Partial<DemoData> | null;
  if (!data || !data.header || data.header.magic !== 'HADEMO') {
    throw new DemoError('This file is not a .hademo demo.');
  }
  if (!Array.isArray(data.frames) || data.frames.length === 0) {
    throw new DemoError(
      'This demo has no frames, so there is nothing to play. Demos saved before ' +
        'recording was fixed are all empty; record a new one.',
    );
  }
  const frames = data.frames.map((f) => ({
    ...f,
    players: (f.players ?? []).map((p) => ({ ...p, id: String(p.id) })),
    events: f.events ?? [],
  }));
  const last = frames[frames.length - 1];
  return {
    header: {
      ...data.header,
      pov: data.header.pov !== undefined ? String(data.header.pov) : undefined,
      durationMs: Math.max(data.header.durationMs || 0, last.timestamp),
    },
    frames,
    bookmarks: Array.isArray(data.bookmarks) ? data.bookmarks : [],
  };
}

/**
 * 6-DOF Drone Freecam Controller
 * Allows flying through the map during replays or spectating with full pitch/yaw/roll and FOV zoom.
 */
export class DroneFreecam {
  public x = 0;
  public y = 0;
  public z = 5;
  public yaw = 0;
  public pitch = 0;
  public roll = 0;
  public fov = 90;

  public vx = 0;
  public vy = 0;
  public vz = 0;
  public vYaw = 0;
  public vPitch = 0;
  public vRoll = 0;
  public vFov = 0;

  public speed = 12.0;
  public friction = 8.0;
  public angularFriction = 12.0;
  public active = false;

  public setPosition(x: number, y: number, z: number, yaw = 0, pitch = 0, roll = 0, fov = 90): void {
    this.x = x;
    this.y = y;
    this.z = z;
    this.yaw = yaw;
    this.pitch = pitch;
    this.roll = roll;
    this.fov = fov;
    this.vx = 0;
    this.vy = 0;
    this.vz = 0;
    this.vYaw = 0;
    this.vPitch = 0;
    this.vRoll = 0;
    this.vFov = 0;
  }

  public update(
    dt: number,
    input: {
      forward?: number;
      strafe?: number;
      vertical?: number;
      yawDelta?: number;
      pitchDelta?: number;
      rollInput?: number;
      fovDelta?: number;
      boost?: boolean;
    },
  ): void {
    if (!this.active) return;
    dt = Math.min(dt, 0.05);

    const moveSpeed = this.speed * (input.boost ? 2.5 : 1.0);

    // Orientation vectors from yaw (yaw 0 = +y)
    const radYaw = (this.yaw * Math.PI) / 180;
    const forwardX = -Math.sin(radYaw);
    const forwardY = Math.cos(radYaw);
    const rightX = Math.cos(radYaw);
    const rightY = Math.sin(radYaw);

    const fwd = input.forward ?? 0;
    const str = input.strafe ?? 0;
    const vert = input.vertical ?? 0;

    const ax = (forwardX * fwd + rightX * str) * moveSpeed;
    const ay = (forwardY * fwd + rightY * str) * moveSpeed;
    const az = vert * moveSpeed;

    // Apply acceleration
    this.vx += ax * dt * 10;
    this.vy += ay * dt * 10;
    this.vz += az * dt * 10;

    // Apply linear friction
    const damp = Math.max(0, 1 - this.friction * dt);
    this.vx *= damp;
    this.vy *= damp;
    this.vz *= damp;

    this.x += this.vx * dt;
    this.y += this.vy * dt;
    this.z += this.vz * dt;

    // Mouse look
    this.yaw += (input.yawDelta ?? 0);
    this.pitch = Math.max(-89.9, Math.min(89.9, this.pitch + (input.pitchDelta ?? 0)));

    // Camera roll (Q/E)
    const rollIn = input.rollInput ?? 0;
    this.vRoll += rollIn * 180 * dt;
    this.vRoll *= Math.max(0, 1 - this.angularFriction * dt);
    this.roll += this.vRoll * dt;

    // FOV zoom (15° to 120°)
    if (input.fovDelta) {
      this.fov = Math.max(15, Math.min(120, this.fov + input.fovDelta));
    }
  }
}

/**
 * Catmull-Rom Spline Dolly Keyframe Path
 */
export class DollyPath {
  public keyframes: DollyKeyframe[] = [];

  public clear(): void {
    this.keyframes = [];
  }

  public addKeyframe(kf: DollyKeyframe): void {
    this.keyframes.push({ ...kf });
    this.keyframes.sort((a, b) => a.time - b.time);
  }

  public evaluate(time: number): DollyKeyframe | null {
    if (this.keyframes.length === 0) return null;
    if (this.keyframes.length === 1) return { ...this.keyframes[0] };

    const first = this.keyframes[0];
    const last = this.keyframes[this.keyframes.length - 1];
    if (time <= first.time) return { ...first };
    if (time >= last.time) return { ...last };

    // Find segment
    let idx = 0;
    for (let i = 0; i < this.keyframes.length - 1; i++) {
      if (time >= this.keyframes[i].time && time <= this.keyframes[i + 1].time) {
        idx = i;
        break;
      }
    }

    const k0 = this.keyframes[Math.max(0, idx - 1)];
    const k1 = this.keyframes[idx];
    const k2 = this.keyframes[idx + 1];
    const k3 = this.keyframes[Math.min(this.keyframes.length - 1, idx + 2)];

    const span = k2.time - k1.time;
    const t = span > 0 ? (time - k1.time) / span : 0;

    return {
      time,
      x: catmullRom(k0.x, k1.x, k2.x, k3.x, t),
      y: catmullRom(k0.y, k1.y, k2.y, k3.y, t),
      z: catmullRom(k0.z, k1.z, k2.z, k3.z, t),
      yaw: interpolateAngle(k1.yaw, k2.yaw, t),
      pitch: interpolateAngle(k1.pitch, k2.pitch, t),
      roll: interpolateAngle(k1.roll, k2.roll, t),
      fov: catmullRom(k0.fov, k1.fov, k2.fov, k3.fov, t),
    };
  }
}

/**
 * Match Demo Recorder
 */
export class DemoRecorder {
  private active = false;
  private header: DemoHeader | null = null;
  private frames: DemoFrame[] = [];
  private bookmarks: DemoBookmark[] = [];
  private currentTick = 0;
  private startTime = 0;
  private pendingEvents: DemoEvent[] = [];
  private lastFrameAt = -Infinity;

  public start(mapName: string, playerNames: Record<string, string> = {}, pov?: string): void {
    this.active = true;
    this.lastFrameAt = -Infinity;
    this.currentTick = 0;
    this.startTime = performance.now();
    this.frames = [];
    this.bookmarks = [];
    this.pendingEvents = [];
    this.header = {
      magic: 'HADEMO',
      version: 1,
      map: mapName,
      recordedAt: Date.now(),
      // Thirty, not the display's rate: playback interpolates between frames,
      // and a minute at sixty with ten bodies is a file nobody wants to share.
      tickRate: 30,
      playerNames: { ...playerNames },
      durationMs: 0,
      pov,
    };
  }

  /** Whether a frame is owed: one per `1 / tickRate`, whatever the frame rate. */
  public due(): boolean {
    if (!this.active || !this.header) return false;
    return performance.now() - this.lastFrameAt >= 1000 / this.header.tickRate;
  }

  public isRecording(): boolean {
    return this.active;
  }

  public recordEvent(event: Omit<DemoEvent, 'tick' | 'timestamp'>): void {
    if (!this.active) return;
    const ev: DemoEvent = {
      ...event,
      tick: this.currentTick,
      timestamp: performance.now() - this.startTime,
    };
    this.pendingEvents.push(ev);

    if (ev.type === 'kill') {
      let label = 'KILL';
      let type: DemoBookmark['type'] = 'kill';
      if (ev.head) {
        label = 'HEADSHOT';
        type = 'headshot';
      } else if (ev.nutshot) {
        label = 'NUTSHOT';
        type = 'nutshot';
      }
      if (ev.streak && ev.streak > 1) {
        label = `STREAK x${ev.streak}`;
        type = 'streak';
      }
      this.bookmarks.push({
        tick: ev.tick,
        timestamp: ev.timestamp,
        label,
        type,
        targetPlayerId: ev.killerId,
      });
    }
  }

  public recordFrame(players: DemoPlayerSnapshot[]): void {
    if (!this.active) return;
    this.lastFrameAt = performance.now();
    const now = this.lastFrameAt - this.startTime;
    this.frames.push({
      tick: this.currentTick++,
      timestamp: now,
      players: players.map((p) => ({ ...p })),
      events: [...this.pendingEvents],
    });
    this.pendingEvents = [];
  }

  public stop(): DemoData | null {
    if (!this.active || !this.header) return null;
    this.active = false;
    const durationMs = performance.now() - this.startTime;
    this.header.durationMs = durationMs;
    return {
      header: { ...this.header },
      frames: [...this.frames],
      bookmarks: [...this.bookmarks],
    };
  }

  public exportJson(): string {
    const data = this.stop();
    return data ? JSON.stringify(data) : '';
  }
}

/**
 * Replay Playback Controller
 */
export class DemoPlayer {
  public demo: DemoData | null = null;
  public isPlaying = false;
  public playbackSpeed = 1.0;
  public currentTimeMs = 0;
  public cleanView = false;
  public freecam = new DroneFreecam();
  public dolly = new DollyPath();
  public spectatorTargetId: string | null = null;

  public load(demo: DemoData): void {
    this.demo = demo;
    this.currentTimeMs = 0;
    this.isPlaying = false;
    this.spectatorTargetId = demo.header.pov ?? demo.frames[0]?.players[0]?.id ?? null;
  }

  /** At the end and stopped: Play then starts again from the top. */
  public get ended(): boolean {
    return !!this.demo && this.currentTimeMs >= this.demo.header.durationMs;
  }

  public play(): void {
    this.isPlaying = true;
  }

  public pause(): void {
    this.isPlaying = false;
  }

  public togglePlay(): void {
    if (!this.isPlaying && this.ended) this.currentTimeMs = 0;
    this.isPlaying = !this.isPlaying;
  }

  public setSpeed(speed: number): void {
    this.playbackSpeed = Math.max(0.1, Math.min(4.0, speed));
  }

  public seek(timeMs: number): void {
    if (!this.demo) return;
    this.currentTimeMs = Math.max(0, Math.min(this.demo.header.durationMs, timeMs));
  }

  public seekRelative(seconds: number): void {
    this.seek(this.currentTimeMs + seconds * 1000);
  }

  public seekToBookmark(index: number): void {
    if (!this.demo || index < 0 || index >= this.demo.bookmarks.length) return;
    const bm = this.demo.bookmarks[index];
    // Seek 2.0 seconds before the frag for prime montage pacing
    this.seek(Math.max(0, bm.timestamp - 2000));
    if (bm.targetPlayerId !== undefined) {
      this.spectatorTargetId = bm.targetPlayerId;
    }
  }

  public toggleCleanView(): boolean {
    this.cleanView = !this.cleanView;
    return this.cleanView;
  }

  public update(dt: number): void {
    if (!this.isPlaying || !this.demo) return;
    this.currentTimeMs += dt * 1000 * this.playbackSpeed;
    if (this.currentTimeMs >= this.demo.header.durationMs) {
      this.currentTimeMs = this.demo.header.durationMs;
      this.isPlaying = false;
    }
  }

  /**
   * Sample interpolated frame at current replay time
   */
  public sampleCurrentFrame(): {
    players: DemoPlayerSnapshot[];
    events: DemoEvent[];
  } {
    if (!this.demo || this.demo.frames.length === 0) {
      return { players: [], events: [] };
    }

    const frames = this.demo.frames;
    const t = this.currentTimeMs;

    if (t <= frames[0].timestamp) {
      return { players: frames[0].players, events: frames[0].events };
    }
    const last = frames[frames.length - 1];
    if (t >= last.timestamp) {
      return { players: last.players, events: last.events };
    }

    // Binary search for surrounding frames
    let low = 0;
    let high = frames.length - 1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (frames[mid].timestamp <= t) {
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }

    const idx0 = Math.max(0, high);
    const idx1 = Math.min(frames.length - 1, idx0 + 1);
    const f0 = frames[idx0];
    const f1 = frames[idx1];

    const span = f1.timestamp - f0.timestamp;
    const alpha = span > 0 ? (t - f0.timestamp) / span : 0;

    // Interpolate players
    const players: DemoPlayerSnapshot[] = [];
    for (const p0 of f0.players) {
      const p1 = f1.players.find((p) => p.id === p0.id);
      if (!p1) {
        players.push({ ...p0 });
        continue;
      }

      players.push({
        id: p0.id,
        name: p0.name,
        team: p0.team,
        x: p0.x + (p1.x - p0.x) * alpha,
        y: p0.y + (p1.y - p0.y) * alpha,
        z: p0.z + (p1.z - p0.z) * alpha,
        yaw: interpolateRadians(p0.yaw, p1.yaw, alpha),
        pitch: interpolateRadians(p0.pitch, p1.pitch, alpha),
        roll: interpolateRadians(p0.roll ?? 0, p1.roll ?? 0, alpha),
        weapon: alpha < 0.5 ? p0.weapon : p1.weapon,
        hp: alpha < 0.5 ? p0.hp : p1.hp,
        crouching: alpha < 0.5 ? p0.crouching : p1.crouching,
        sprinting: alpha < 0.5 ? p0.sprinting : p1.sprinting,
        sliding: alpha < 0.5 ? p0.sliding : p1.sliding,
        crouch: (p0.crouch ?? 0) + ((p1.crouch ?? 0) - (p0.crouch ?? 0)) * alpha,
        alive: alpha < 0.5 ? p0.alive : p1.alive,
      });
    }

    return { players, events: f0.events };
  }
}
