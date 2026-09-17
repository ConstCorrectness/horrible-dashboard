/**
 * Timeline geometry.
 *
 * A chart is the one view where a wrong answer is invisible: bars in plausible
 * positions look like data whichever numbers produced them. So the properties that
 * would silently mislead get pinned here rather than eyeballed in the pane.
 */
import { describe, expect, it } from 'vitest';

import { axisTicks, buildTimeline } from '../panels/timeline-model';
import type { TrajectoryStep } from '../api';

/** `ts` is epoch **seconds**; `duration_ms` is milliseconds. Mixing the two units is
 * the easiest mistake to make against this shape, so the helper names both. */
function step(over: Partial<TrajectoryStep> & { seq: number; ts: number }): TrajectoryStep {
  return {
    kind: 'action',
    round: 0,
    role: null,
    name: 'tool',
    args: null,
    result: null,
    ok: true,
    content: null,
    tokens: null,
    duration_ms: 100,
    gated: false,
    error: null,
    ...over,
  } as TrajectoryStep;
}

describe('buildTimeline', () => {
  it('places a bar at its offset from the run start, not at index', () => {
    const model = buildTimeline([
      step({ seq: 0, ts: 1000, duration_ms: 1000 }),
      step({ seq: 1, ts: 1002, duration_ms: 1000 }),
    ]);
    // 1000s → 1003s: the second call starts 2s in and runs for 1s.
    expect(model.spanMs).toBe(3000);
    const [a, b] = model.rounds[0].bars;
    expect(a.left).toBeCloseTo(0);
    expect(b.left).toBeCloseTo(2 / 3);
    expect(b.width).toBeCloseTo(1 / 3);
  });

  it('leaves the gap where the model was thinking', () => {
    // The run began a second before its first tool call. That second is the model
    // deciding, and collapsing it would attribute think time to the tool.
    const model = buildTimeline([step({ seq: 0, ts: 1001, duration_ms: 1000 })], 1000);
    expect(model.startedAt).toBe(1000);
    expect(model.rounds[0].bars[0].left).toBeCloseTo(0.5);
  });

  it('ignores a run start that postdates the first step', () => {
    // An imported run carries another machine's clock. Trusting it blindly yields a
    // negative offset and a bar drawn off the left edge of the chart.
    const model = buildTimeline([step({ seq: 0, ts: 1000, duration_ms: 500 })], 9999);
    expect(model.startedAt).toBe(1000);
    expect(model.rounds[0].bars[0].left).toBe(0);
  });

  it('groups bars into rounds and orders both', () => {
    const model = buildTimeline([
      step({ seq: 2, ts: 3000, round: 1 }),
      step({ seq: 0, ts: 1000, round: 0 }),
      step({ seq: 1, ts: 2000, round: 0 }),
    ]);
    expect(model.rounds.map((r) => r.round)).toEqual([0, 1]);
    expect(model.rounds[0].bars.map((b) => b.seq)).toEqual([0, 1]);
  });

  it('keeps an instant visible and says that is what it is', () => {
    // A message has no duration. Drawn at its true width it is zero pixels wide and
    // simply absent from the chart, which reads as a missing step.
    const model = buildTimeline([
      step({ seq: 0, ts: 1000, kind: 'message', duration_ms: null, name: null, role: 'assistant' }),
      step({ seq: 1, ts: 1000, duration_ms: 5000 }),
    ]);
    const instant = model.rounds[0].bars[0];
    expect(instant.instant).toBe(true);
    expect(instant.width).toBeGreaterThan(0);
    expect(instant.label).toBe('assistant');
  });

  it('never divides by a zero span', () => {
    // One instantaneous step is a whole run's worth of data for a run that answered
    // without acting, and every offset here is a division by the span.
    const model = buildTimeline([step({ seq: 0, ts: 1000, duration_ms: null })]);
    expect(model.spanMs).toBeGreaterThan(0);
    expect(Number.isFinite(model.rounds[0].bars[0].left)).toBe(true);
  });

  it('drops steps with no usable timestamp rather than stacking them at zero', () => {
    const model = buildTimeline([step({ seq: 0, ts: 0 }), step({ seq: 1, ts: 1000 })]);
    expect(model.rounds[0].bars.map((b) => b.seq)).toEqual([1]);
  });

  it('returns an empty model rather than throwing on a run with no steps', () => {
    expect(buildTimeline([]).rounds).toEqual([]);
  });

  it('carries failure and gating through to the bar', () => {
    const model = buildTimeline([
      step({ seq: 0, ts: 1000, ok: false }),
      step({ seq: 1, ts: 1000, gated: true }),
    ]);
    expect(model.rounds[0].bars[0].failed).toBe(true);
    expect(model.rounds[0].bars[1].gated).toBe(true);
  });
});

describe('axisTicks', () => {
  it('labels in ms below a second and seconds above', () => {
    expect(axisTicks(400).at(-1)?.label).toBe('400ms');
    expect(axisTicks(8000).at(-1)?.label).toBe('8.0s');
  });
});
