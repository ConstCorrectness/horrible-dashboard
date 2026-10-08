/** The pure halves of the GPU code: chunk planning and dispatch sizing. */
import { describe, expect, it } from 'vitest';

import { paddedSize, planRowChunks, tensorChunks } from '../chunks';
import { dispatch1d } from '../dispatch';

describe('planRowChunks', () => {
  it('is one chunk when the tensor fits', () => {
    expect(planRowChunks(10, 100, 1000)).toEqual([
      { firstRow: 0, rows: 10, byteOffset: 0, byteLength: 1000 },
    ]);
  });

  it('splits by whole rows and covers every row once', () => {
    const chunks = planRowChunks(10, 300, 1000);
    expect(chunks.map((c) => c.rows)).toEqual([3, 3, 3, 1]);
    expect(chunks.map((c) => c.firstRow)).toEqual([0, 3, 6, 9]);
    expect(chunks.map((c) => c.byteOffset)).toEqual([0, 900, 1800, 2700]);
    for (const c of chunks) expect(paddedSize(c.byteLength)).toBeLessThanOrEqual(1000);
  });

  it('keeps the padded size within the limit for odd row sizes', () => {
    // Q8_0 rows of 32 elements are 34 bytes; 1003 / 34 = 29.5, but 29 * 34 = 986 → 988.
    for (const c of planRowChunks(100, 34, 1003)) {
      expect(paddedSize(c.byteLength)).toBeLessThanOrEqual(1003);
    }
  });

  it('refuses a row larger than a binding', () => {
    expect(() => planRowChunks(2, 2000, 1000)).toThrow(/one row is 2000 bytes/);
  });

  it('chunks a GGUF tensor by its quant row size', () => {
    // Q6_K, 256-element rows = 210 bytes; 151,936 rows of a vocabulary.
    const t = {
      name: 'output.weight',
      shape: [256, 151_936],
      type: 14,
      typeName: 'Q6_K',
      offset: 0,
      elements: 256 * 151_936,
      bytes: 210 * 151_936,
    };
    const chunks = tensorChunks(t, 8 * 1024 * 1024);
    expect(chunks.reduce((n, c) => n + c.rows, 0)).toBe(151_936);
    expect(chunks.length).toBe(
      Math.ceil((210 * 151_936) / (Math.floor((8 * 1024 * 1024) / 210) * 210)),
    );
    expect(() => tensorChunks({ ...t, type: 99, typeName: 'type:99' }, 1 << 20)).toThrow(
      /no row size/,
    );
  });
});

describe('dispatch1d', () => {
  it('stays in x while it can, and spills into y past the per-dimension limit', () => {
    expect(dispatch1d(1, 256, 65_535)).toEqual([1, 1]);
    expect(dispatch1d(0, 256, 65_535)).toEqual([1, 1]);
    expect(dispatch1d(256 * 65_535, 256, 65_535)).toEqual([65_535, 1]);
    const [x, y] = dispatch1d(256 * 65_535 + 1, 256, 65_535);
    expect(y).toBe(2);
    expect(x * y * 256).toBeGreaterThanOrEqual(256 * 65_535 + 1);
    expect(x).toBeLessThanOrEqual(65_535);
  });

  it('refuses a job no dispatch can cover', () => {
    expect(() => dispatch1d(10_000, 1, 10)).toThrow(/more than one dispatch/);
  });
});
