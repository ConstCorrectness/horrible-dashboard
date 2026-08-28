import { describe, expect, it } from 'vitest';

import { RAMP_FLOOR, RAMP_RANGE, rampCell, rampProps, rampScale } from '../ramp';

describe('rampCell', () => {
  it('reads the sign off the value', () => {
    expect(rampCell(1, 2).sign).toBe('pos');
    expect(rampCell(-1, 2).sign).toBe('neg');
  });

  it('gives a genuine zero the neutral cell', () => {
    expect(rampCell(0, 2)).toEqual({ sign: 'zero', alpha: 0 });
  });

  /* Nothing measured a scale, so there is nothing to be strong RELATIVE TO. Full
     strength here would be a confident drawing of an unknown. */
  it('refuses to draw strength without a scale', () => {
    expect(rampCell(500, 0)).toEqual({ sign: 'zero', alpha: 0 });
  });

  it('clamps beyond the scale instead of running past full strength', () => {
    expect(rampCell(99, 1).alpha).toBeCloseTo(RAMP_FLOOR + RAMP_RANGE);
    expect(rampCell(-99, 1).alpha).toBeCloseTo(RAMP_FLOOR + RAMP_RANGE);
  });

  it('keeps a small value visible via the floor', () => {
    expect(rampCell(0.001, 1000).alpha).toBeGreaterThanOrEqual(RAMP_FLOOR);
  });

  it('is symmetric in magnitude', () => {
    expect(rampCell(0.5, 1).alpha).toBeCloseTo(rampCell(-0.5, 1).alpha);
  });

  it('treats a non-finite value as unmeasured', () => {
    expect(rampCell(NaN, 1).sign).toBe('zero');
    expect(rampCell(1, Infinity).sign).toBe('zero');
  });
});

describe('rampScale', () => {
  it('is the largest magnitude, sign ignored', () => {
    expect(rampScale([1, -9, 3])).toBe(9);
  });

  it('is zero for an empty strip, which rampCell then refuses to draw', () => {
    expect(rampScale([])).toBe(0);
  });
});

describe('rampProps', () => {
  /* The sign and the alpha have to travel together: applying the sign alone would
     leave `--viz-a` at its CSS default and paint every cell the same. */
  it('carries the alpha with the sign', () => {
    const props = rampProps(rampCell(1, 1));
    expect(props['data-sign']).toBe('pos');
    expect(props.style['--viz-a']).toBe(String(RAMP_FLOOR + RAMP_RANGE));
  });
});
