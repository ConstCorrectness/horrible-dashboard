import { describe, expect, it } from 'vitest';

import { poolMaxAbs, poolMean } from '../pool';

describe('poolMean', () => {
  it('passes a short series through untouched and says it did not pool', () => {
    expect(poolMean([1, 2, 3], 8)).toEqual({ cells: [1, 2, 3], pooled: false, factor: 1 });
  });

  it('averages each bucket and reports the factor', () => {
    const out = poolMean([0, 2, 4, 6], 2);
    expect(out.cells).toEqual([1, 5]);
    expect(out.pooled).toBe(true);
    expect(out.factor).toBe(2);
  });

  it('covers every input value, leaving none out of the picture', () => {
    const values = Array.from({ length: 1000 }, () => 1);
    const out = poolMean(values, 192);
    expect(out.cells).toHaveLength(192);
    expect(out.cells.every((c) => c === 1)).toBe(true);
  });
});

describe('poolMaxAbs', () => {
  /* An attention row is mostly near-zero with one or two spikes. A mean erases
     exactly the thing the view was opened to see. */
  it('keeps the spike a mean would erase', () => {
    expect(poolMaxAbs([0, 0, 0, 9], 1).cells).toEqual([9]);
    expect(poolMean([0, 0, 0, 9], 1).cells).toEqual([2.25]);
  });

  /* The magnitude decides the winner but the winner keeps its sign — otherwise a
     strip of a signed quantity would come back uniformly positive. */
  it('preserves the sign of the winning value', () => {
    expect(poolMaxAbs([1, -9, 3], 1).cells).toEqual([-9]);
  });
});
