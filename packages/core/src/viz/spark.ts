/**
 * Sparkline geometry, split out from the drawing.
 *
 * The rule this file exists to protect: **a gap is drawn as a gap.** A pass whose
 * record was summarized without a stored statistic has nothing to report, and
 * joining the line across it would draw a measurement that was never taken. That
 * was a comment above a render in `TracesSection.tsx`; here it is a function with
 * a test, which is the difference between a rule and an intention.
 *
 * A lone measured point between two gaps is a dot, because a one-point polyline
 * draws nothing at all — silently, which is the worst way for a chart to be wrong.
 */

export interface SparkPoint {
  x: number;
  /** `null` is "not measured". It is never 0 — that is a measurement. */
  y: number | null;
}

export interface SparkGeometry {
  /** Runs of adjacent measured points, each a list of `"x,y"` in view units. */
  runs: string[][];
  /** The y domain actually drawn against. */
  lo: number;
  hi: number;
  measured: number;
}

/**
 * `domain` overrides the autoscale. `SmallMultiples` passes a shared one: N
 * independently-scaled sparklines look comparable and are not, which is a lie the
 * caller has to be able to refuse.
 */
export function sparkRuns(
  points: SparkPoint[],
  width: number,
  height: number,
  domain?: readonly [number, number],
): SparkGeometry {
  const measured = points.filter((p) => p.y !== null && Number.isFinite(p.y));
  if (measured.length === 0) return { runs: [], lo: 0, hi: 0, measured: 0 };

  const values = measured.map((p) => p.y as number);
  const lo = domain ? domain[0] : Math.min(...values);
  const hi = domain ? domain[1] : Math.max(...values);
  const span = hi - lo || 1;

  const xs = points.map((p) => p.x);
  const xLo = Math.min(...xs);
  const xHi = Math.max(...xs);
  const xSpan = xHi - xLo || 1;

  // Inset by 1 unit so a stroke at the extreme is not clipped in half.
  const px = (x: number) => ((x - xLo) / xSpan) * (width - 2) + 1;
  const py = (y: number) => height - 1 - ((y - lo) / span) * (height - 2);

  const runs: string[][] = [];
  let run: string[] = [];
  for (const point of points) {
    if (point.y === null || !Number.isFinite(point.y)) {
      if (run.length) runs.push(run);
      run = [];
      continue;
    }
    run.push(`${px(point.x).toFixed(1)},${py(point.y).toFixed(1)}`);
  }
  if (run.length) runs.push(run);

  return { runs, lo, hi, measured: measured.length };
}

/** The union domain over several series — what makes small multiples comparable. */
export function sharedDomain(series: readonly SparkPoint[][]): [number, number] | undefined {
  let lo = Infinity;
  let hi = -Infinity;
  for (const points of series) {
    for (const p of points) {
      if (p.y === null || !Number.isFinite(p.y)) continue;
      if (p.y < lo) lo = p.y;
      if (p.y > hi) hi = p.y;
    }
  }
  return Number.isFinite(lo) ? [lo, hi] : undefined;
}
