import { describe, expect, it } from 'vitest';

import { sharedDomain, sparkRuns, type SparkPoint } from '../spark';

const W = 100;
const H = 20;

function pts(...ys: (number | null)[]): SparkPoint[] {
  return ys.map((y, x) => ({ x, y }));
}

describe('sparkRuns', () => {
  it('draws one run when everything was measured', () => {
    const geo = sparkRuns(pts(1, 2, 3, 4), W, H);
    expect(geo.runs).toHaveLength(1);
    expect(geo.runs[0]).toHaveLength(4);
    expect(geo.measured).toBe(4);
  });

  /* The rule this module exists for: joining across an unmeasured pass would draw
     a measurement that was never taken. */
  it('splits the line at a gap rather than joining across it', () => {
    const geo = sparkRuns(pts(1, 2, null, 4, 5), W, H);
    expect(geo.runs).toHaveLength(2);
    expect(geo.runs[0]).toHaveLength(2);
    expect(geo.runs[1]).toHaveLength(2);
  });

  /* A one-point polyline draws nothing at all — silently. The caller renders a
     dot, and can only know to when the run is reported separately. */
  it('reports a lone measured point between two gaps as its own run', () => {
    const geo = sparkRuns(pts(null, 7, null), W, H);
    expect(geo.runs).toHaveLength(1);
    expect(geo.runs[0]).toHaveLength(1);
  });

  it('treats a non-finite value as a gap, not as a number', () => {
    const geo = sparkRuns(pts(1, NaN as unknown as number, 3), W, H);
    expect(geo.runs).toHaveLength(2);
    expect(geo.measured).toBe(2);
  });

  it('says nothing when nothing was measured', () => {
    expect(sparkRuns(pts(null, null), W, H)).toMatchObject({ runs: [], measured: 0 });
  });

  it('autoscales to the measured extremes, low at the bottom', () => {
    const geo = sparkRuns(pts(10, 20), W, H);
    expect(geo.lo).toBe(10);
    expect(geo.hi).toBe(20);
    const [, y0] = geo.runs[0][0].split(',').map(Number);
    const [, y1] = geo.runs[0][1].split(',').map(Number);
    expect(y0).toBeGreaterThan(y1);
  });

  /* A flat series has no span; dividing by it would put every point at NaN and the
     chart would vanish rather than show a flat line. */
  it('survives a flat series', () => {
    const geo = sparkRuns(pts(5, 5, 5), W, H);
    expect(geo.runs[0].every((c) => Number.isFinite(Number(c.split(',')[1])))).toBe(true);
  });

  it('honours a supplied domain so several charts stay comparable', () => {
    const a = sparkRuns(pts(1, 2), W, H, [0, 10]);
    const b = sparkRuns(pts(1, 2), W, H);
    expect(a.lo).toBe(0);
    expect(a.hi).toBe(10);
    // Autoscaled, the series minimum is pinned to the floor of the box. Given a
    // wider domain it lifts off it, which is the whole point: the position now
    // means something across charts instead of only within this one.
    const floor = Number(b.runs[0][0].split(',')[1]);
    expect(Number(a.runs[0][0].split(',')[1])).toBeLessThan(floor);
  });
});

describe('sharedDomain', () => {
  it('spans every series, ignoring gaps', () => {
    expect(sharedDomain([pts(1, null, 4), pts(-2, 9)])).toEqual([-2, 9]);
  });

  it('is undefined when nothing was measured, so callers autoscale instead', () => {
    expect(sharedDomain([pts(null), pts(null)])).toBeUndefined();
  });
});
