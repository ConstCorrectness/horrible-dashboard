/**
 * Aim drills on the training range: replaying a served schedule, and scoring.
 *
 * The decisions are the server's (`backend/modules/hassault/drills.py`): when a
 * target turns back, when a pop-up shows. What a target *does* with an input is
 * this client's own `step`, run one fixed tick at a time, so it accelerates and
 * counter-strafes exactly like a player because it runs the same legs.
 * `drill-vectors.json` pins both halves against the Python: the replayed
 * positions, and `summarise`.
 *
 * Free of three and of React, like the rest of the game's logic files.
 */
import { createPlayer, step, type PlayerState } from './player';
import type { World } from './world';

/** One input segment: `[tick, strafe, crouch, jump, visible]`, from that tick on. */
export type DrillSegment = [number, number, number, number, number];

export interface DrillTargetSpec {
  id: string;
  x: number;
  y: number;
  z: number;
  yaw: number;
  segments: DrillSegment[];
}

/** A drill as listed: what the picker shows. */
export interface DrillSummary {
  id: string;
  name: string;
  description: string;
  pattern: string;
  /** The map it runs on, or `null` for any map. */
  map: string | null;
  station: string | null;
  seconds: number;
  hp: number;
  headOnly: boolean;
  /** Seconds a downed target stays down. */
  respawn: number;
}

/** A drill expanded for one run: `GET /api/hassault/drills/{id}`. */
export interface DrillSpec extends DrillSummary {
  map: string;
  tickRate: number;
  ticks: number;
  /** Where to stand, on a map with stations; `null` on the any-map drill. */
  shooter: { x: number; y: number; z: number; yaw: number } | null;
  targets: DrillTargetSpec[];
}

/** One target being replayed. */
export class DrillMover {
  readonly state: PlayerState;
  private seg = 0;
  private tick = 0;

  constructor(
    readonly spec: DrillTargetSpec,
    private readonly tickDt: number,
  ) {
    this.state = createPlayer(spec.x, spec.y, spec.z, spec.yaw);
    // `drills._stand` places the body on the ground, as `spawn_at` does. A
    // fresh `createPlayer` is airborne, and the first replayed step would then
    // be a fall rather than a strafe.
    this.state.onGround = true;
  }

  /** The segment in force at the current tick. */
  current(): DrillSegment {
    return this.spec.segments[this.seg] ?? [0, 0, 0, 0, 1];
  }

  /** Whether the schedule shows this target right now. */
  get visible(): boolean {
    return this.current()[4] !== 0;
  }

  /** Run one fixed tick: pick the input for this tick, then step. */
  advance(world: World): void {
    const segments = this.spec.segments;
    while (this.seg + 1 < segments.length && segments[this.seg + 1][0] <= this.tick) {
      this.seg += 1;
    }
    const [, strafe, crouch, jump] = this.current();
    step(
      world,
      this.state,
      { forward: 0, strafe, jump: jump !== 0, crouch: crouch !== 0, noclip: false },
      this.tickDt,
    );
    this.tick += 1;
  }
}

// ---- scoring --------------------------------------------------------------------

export interface DrillHit {
  target: string;
  head: boolean;
  killed: boolean;
}

export type DrillEvent =
  | { t: number; kind: 'appear'; target: string }
  | { t: number; kind: 'hide'; target: string }
  | { t: number; kind: 'shot'; hits: DrillHit[] };

export interface DrillStats {
  shots: number;
  hitShots: number;
  accuracy: number;
  hits: number;
  headHits: number;
  headshotShare: number;
  kills: number;
  killsPerMinute: number;
  medianTtk: number | null;
  medianReaction: number | null;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Score one run from its event log. A port of `drills.summarise`; see its
 * docstring for what each number means. Pinned by `drill-vectors.json`.
 */
export function summarise(events: DrillEvent[], seconds: number): DrillStats {
  let shots = 0;
  let hitShots = 0;
  let hits = 0;
  let headHits = 0;
  let kills = 0;
  const shownAt = new Map<string, number>();
  const firstHit = new Map<string, number>();
  const ttks: number[] = [];
  const reactions: number[] = [];
  for (const event of events) {
    const t = event.t;
    if (event.kind === 'appear') {
      shownAt.set(event.target, t);
      firstHit.delete(event.target);
    } else if (event.kind === 'hide') {
      shownAt.delete(event.target);
      firstHit.delete(event.target);
    } else {
      shots += 1;
      if (event.hits.length > 0) hitShots += 1;
      for (const hit of event.hits) {
        hits += 1;
        if (hit.head) headHits += 1;
        const shown = shownAt.get(hit.target);
        if (shown !== undefined && !firstHit.has(hit.target)) {
          firstHit.set(hit.target, t);
          reactions.push(t - shown);
        }
        if (hit.killed) {
          kills += 1;
          const first = firstHit.get(hit.target);
          if (first !== undefined) ttks.push(t - first);
          shownAt.delete(hit.target);
          firstHit.delete(hit.target);
        }
      }
    }
  }
  const ratio = (a: number, b: number) => (b ? a / b : 0);
  return {
    shots,
    hitShots,
    accuracy: ratio(hitShots, shots),
    hits,
    headHits,
    headshotShare: ratio(headHits, hits),
    kills,
    killsPerMinute: seconds > 0 ? (kills * 60) / seconds : 0,
    medianTtk: median(ttks),
    medianReaction: median(reactions),
  };
}

// ---- personal bests -------------------------------------------------------------

const BEST_KEY = 'hassault.drills.best';

/**
 * This browser's best kills-per-minute on each drill.
 *
 * Browser storage on purpose, and only until account data lives on the game
 * server: a drill is scored by the client that ran it, so its numbers are a
 * personal record and never feed XP, credits or a rank. Every read and write is
 * guarded, because storage can throw (a private window, blocked site data).
 */
export function readBests(): Record<string, DrillStats> {
  try {
    const raw = globalThis.localStorage?.getItem(BEST_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, DrillStats>) : {};
  } catch {
    return {};
  }
}

/** Record `stats` if it beats the stored best; returns whether it did. */
export function recordBest(drillId: string, stats: DrillStats): boolean {
  const bests = readBests();
  const prior = bests[drillId];
  const better =
    !prior ||
    stats.killsPerMinute > prior.killsPerMinute ||
    (stats.killsPerMinute === prior.killsPerMinute && stats.accuracy > prior.accuracy);
  if (!better) return false;
  bests[drillId] = stats;
  try {
    globalThis.localStorage?.setItem(BEST_KEY, JSON.stringify(bests));
  } catch {
    // A best that cannot be kept is still shown for this run.
  }
  return true;
}
