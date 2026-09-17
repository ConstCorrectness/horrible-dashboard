/**
 * The geometry behind the run timeline, kept out of the component so it can be
 * tested without a DOM.
 *
 * ## Why a waterfall is derivable at all
 *
 * The orchestrator runs a round's tool calls strictly in sequence — one `await` per
 * call — so within a run there is no concurrency to reconstruct. `(round, ts,
 * duration_ms)` is therefore a complete description of the layout, and the only
 * nesting that exists is delegation, which lives on `parent_run_id` rather than
 * between steps.
 *
 * ## `ts` is the start, and that is load-bearing
 *
 * `rec.action` is called after the tool has been awaited, so `ts` used to be the
 * moment the call *ended*. A bar drawn from that sits one full duration to the right
 * of the truth — a chart that is wrong and looks entirely reasonable. The recorder
 * now stamps the start explicitly; this file assumes it.
 *
 * ## Steps with no duration still occupy the timeline
 *
 * A `message` step is an instant, not a zero-length error. It gets a minimum width
 * so it remains clickable and visible, and `instant` marks it so the renderer can
 * say so rather than implying a bar too short to read.
 */
import type { TrajectoryStep } from '../api';

export interface TimelineBar {
  seq: number;
  round: number;
  kind: TrajectoryStep['kind'];
  label: string;
  /** Fraction of the run's span, 0–1. */
  left: number;
  /** Fraction of the run's span, 0–1. Never zero — see `instant`. */
  width: number;
  /** True when the step has no measured duration: a moment, not a span. */
  instant: boolean;
  failed: boolean;
  gated: boolean;
  durationMs: number | null;
}

export interface TimelineRound {
  round: number;
  bars: TimelineBar[];
}

export interface TimelineModel {
  rounds: TimelineRound[];
  /** Wall-clock start of the whole run, in epoch seconds. */
  startedAt: number;
  /** Total span in milliseconds. Never zero, so callers can divide by it. */
  spanMs: number;
}

/** Bars thinner than this fraction of the span are widened to stay clickable. */
const MIN_WIDTH = 0.004;

function labelFor(step: TrajectoryStep): string {
  if (step.name) return step.name;
  if (step.kind === 'message') return step.role ?? 'message';
  return step.kind;
}

/**
 * Lay a run's steps out as rounds of bars.
 *
 * `startedAt` is the run's own start when known. Passing it matters: a run whose
 * first recorded step happens a second after the turn began would otherwise draw
 * that step hard against the left edge, hiding the gap where the model was thinking.
 */
export function buildTimeline(steps: TrajectoryStep[], runStartedAt?: number): TimelineModel {
  const timed = steps.filter((s) => Number.isFinite(s.ts) && s.ts > 0);
  if (!timed.length) return { rounds: [], startedAt: runStartedAt ?? 0, spanMs: 1 };

  const firstStep = Math.min(...timed.map((s) => s.ts));
  // Only trust the run's own start when it precedes the first step. An imported run
  // can carry a `started_at` from another machine's clock, and a start *after* the
  // first step would produce negative offsets and bars off the left of the chart.
  const start =
    runStartedAt && runStartedAt > 0 && runStartedAt <= firstStep ? runStartedAt : firstStep;
  const end = Math.max(...timed.map((s) => s.ts + (s.duration_ms ?? 0) / 1000));
  const spanMs = Math.max(1, (end - start) * 1000);

  const byRound = new Map<number, TimelineBar[]>();
  for (const step of timed) {
    const offsetMs = (step.ts - start) * 1000;
    const rawWidth = (step.duration_ms ?? 0) / spanMs;
    const bars = byRound.get(step.round) ?? [];
    bars.push({
      seq: step.seq,
      round: step.round,
      kind: step.kind,
      label: labelFor(step),
      left: Math.min(1, Math.max(0, offsetMs / spanMs)),
      width: Math.min(1, Math.max(MIN_WIDTH, rawWidth)),
      instant: step.duration_ms == null,
      failed: step.ok === false,
      gated: step.gated,
      durationMs: step.duration_ms,
    });
    byRound.set(step.round, bars);
  }

  const rounds = [...byRound.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([round, bars]) => ({ round, bars: bars.sort((x, y) => x.seq - y.seq) }));

  return { rounds, startedAt: start, spanMs };
}

/** Tick positions for the axis, as fractions of the span. */
export function axisTicks(spanMs: number, count = 4): { at: number; label: string }[] {
  const ticks: { at: number; label: string }[] = [];
  for (let i = 1; i <= count; i += 1) {
    const at = i / count;
    const value = spanMs * at;
    ticks.push({
      at,
      label: value < 1000 ? `${Math.round(value)}ms` : `${(value / 1000).toFixed(1)}s`,
    });
  }
  return ticks;
}
