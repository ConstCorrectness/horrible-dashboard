import { describe, expect, it } from 'vitest';

import { narrowLayers, rangeOf, strideLayers, suggestedStride } from '../narrow';

const deep = Array.from({ length: 262 }, (_, i) => i);
const shallow = Array.from({ length: 32 }, (_, i) => i);

describe('strideLayers', () => {
  it('sends nothing when no narrowing is asked for', () => {
    // An empty list means "all of them" to `getLensGrid`, so the common case
    // sends no parameter rather than one naming every layer.
    expect(strideLayers(shallow, 1)).toEqual([]);
  });

  it('samples every step-th layer', () => {
    expect(strideLayers([0, 1, 2, 3, 4, 5], 2)).toEqual([0, 2, 4, 5]);
  });

  /* The last layer is the model's actual output. A stride that skipped it would
     draw a lens whose final row is a prediction the model never made. */
  it('always keeps the last layer', () => {
    expect(strideLayers([0, 1, 2, 3, 4, 5, 6], 3)).toContain(6);
    expect(strideLayers(deep, 7).at(-1)).toBe(261);
  });

  it('does not duplicate the last layer when the stride already lands on it', () => {
    const out = strideLayers([0, 1, 2, 3, 4], 2);
    expect(out).toEqual([0, 2, 4]);
    expect(new Set(out).size).toBe(out.length);
  });
});

describe('suggestedStride', () => {
  it('leaves a model that already fits alone', () => {
    expect(suggestedStride(32)).toBe(1);
  });

  it('brings a deep model under the row budget', () => {
    const step = suggestedStride(262, 48);
    expect(step).toBeGreaterThan(1);
    expect(strideLayers(deep, step).length).toBeLessThanOrEqual(49);
  });
});

describe('rangeOf', () => {
  it('keeps an inclusive window', () => {
    expect(rangeOf(shallow, 4, 7)).toEqual([4, 5, 6, 7]);
  });

  it('tolerates a reversed range', () => {
    expect(rangeOf(shallow, 7, 4)).toEqual([4, 5, 6, 7]);
  });

  it('sends nothing when the window covers everything', () => {
    expect(rangeOf(shallow, 0, 31)).toEqual([]);
  });
});

describe('narrowLayers', () => {
  it('sends nothing when nothing was narrowed', () => {
    expect(narrowLayers(shallow)).toEqual([]);
  });

  /* Order is load-bearing: the window is applied first, THEN the stride samples
     within it. Striding first and clipping after would give a different row count
     depending on where the window sat, so widening it could make the picture
     coarser — the opposite of what the control says it does. */
  it('windows first and strides within the window', () => {
    expect(narrowLayers(shallow, { from: 10, to: 19, step: 2 })).toEqual([10, 12, 14, 16, 18, 19]);
  });

  it('gives the same row count wherever an equal-width window sits', () => {
    const a = narrowLayers(deep, { from: 0, to: 39, step: 4 }).length;
    const b = narrowLayers(deep, { from: 100, to: 139, step: 4 }).length;
    expect(a).toBe(b);
  });

  it('applies a stride across the whole depth when no window is given', () => {
    const out = narrowLayers(deep, { step: 8 });
    expect(out[0]).toBe(0);
    expect(out.at(-1)).toBe(261);
    expect(out.length).toBeLessThan(40);
  });
});
