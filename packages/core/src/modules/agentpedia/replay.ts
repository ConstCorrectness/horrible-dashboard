/**
 * Replaying a turn: the arithmetic behind the stepper's play button.
 *
 * Progress is one number, `t`, in **round units**: 2.35 means a third of the way
 * through round 2. Everything is derived from it — which round is showing, how many
 * of that round's steps have appeared, where the playhead sits on the timeline — so
 * the play loop only ever advances `t`, and scrubbing only ever sets it.
 *
 * Two clocks, deliberately. The timeline is drawn to **real** time (a round's width
 * is its wall time), because "round 3 took most of the turn" is a finding. Playback
 * is **paced** — each round gets a couple of seconds plus a beat per step — because
 * replaying a 40-second round in real time is watching nothing happen. The playhead
 * maps paced progress onto the real-time track, so both stay true.
 */

export interface ReplayRound {
  /** Wall time of the round in ms, when the recorder had it. */
  wallMs: number | null;
  /** Steps (tool calls, results) the round recorded. */
  steps: number;
}

/** Seconds a round takes to play at 1×: a base, plus a beat per step, capped. */
export function pacedSeconds(round: ReplayRound): number {
  return Math.min(6, 1.6 + 0.45 * round.steps);
}

/**
 * Each round's share of the timeline, summing to 1. Proportional to wall time;
 * rounds without one get the mean of those with one (or all equal when none
 * have it). A floor keeps a 20ms round wide enough to see and click.
 */
export function segmentWeights(rounds: ReplayRound[]): number[] {
  if (!rounds.length) return [];
  const known = rounds.map((r) => r.wallMs).filter((w): w is number => w != null && w > 0);
  const fallback = known.length ? known.reduce((a, b) => a + b, 0) / known.length : 1;
  const raw = rounds.map((r) => (r.wallMs != null && r.wallMs > 0 ? r.wallMs : fallback));
  const total = raw.reduce((a, b) => a + b, 0);
  const floor = 0.4 / rounds.length;
  const floored = raw.map((w) => Math.max(floor, w / total));
  const sum = floored.reduce((a, b) => a + b, 0);
  return floored.map((w) => w / sum);
}

/** The round and the fraction through it at progress `t`, clamped to the turn. */
export function locate(t: number, rounds: number): { round: number; fraction: number } {
  if (rounds <= 0) return { round: 0, fraction: 0 };
  const clamped = Math.max(0, Math.min(t, rounds));
  if (clamped >= rounds) return { round: rounds - 1, fraction: 1 };
  const round = Math.floor(clamped);
  return { round, fraction: clamped - round };
}

/**
 * How many of a round's steps have appeared at `fraction` through it. The first
 * appears a beat in rather than at once, and all of them are out before the
 * round ends, so the last one is on screen for a moment before the next round.
 */
export function revealed(steps: number, fraction: number): number {
  if (steps <= 0) return 0;
  const usable = Math.max(0, Math.min(1, (fraction - 0.12) / 0.7));
  return Math.min(steps, Math.floor(usable * steps) + (fraction >= 0.12 ? 1 : 0));
}

/** Where progress `t` sits on the real-time timeline, 0–1. */
export function playheadAt(t: number, weights: number[]): number {
  const { round, fraction } = locate(t, weights.length);
  let start = 0;
  for (let i = 0; i < round; i++) start += weights[i];
  return Math.min(1, start + (weights[round] ?? 0) * fraction);
}

/** The inverse: the progress at a point `x` (0–1) on the timeline. */
export function progressAt(x: number, weights: number[]): number {
  let start = 0;
  for (let i = 0; i < weights.length; i++) {
    const w = weights[i];
    if (x <= start + w || i === weights.length - 1) {
      return i + Math.max(0, Math.min(1, w ? (x - start) / w : 0));
    }
    start += w;
  }
  return 0;
}

/** Advance `t` by `dtSeconds` of wall clock at `speed`, through paced rounds. */
export function advance(
  t: number,
  dtSeconds: number,
  speed: number,
  rounds: ReplayRound[],
): number {
  let left = dtSeconds * speed;
  let at = t;
  while (left > 0 && at < rounds.length) {
    const { round, fraction } = locate(at, rounds.length);
    const seconds = pacedSeconds(rounds[round]);
    const remaining = (1 - fraction) * seconds;
    if (left < remaining) return at + left / seconds;
    left -= remaining;
    at = round + 1;
  }
  return Math.min(at, rounds.length);
}
