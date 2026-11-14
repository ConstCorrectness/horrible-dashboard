import { describe, expect, it } from 'vitest';

import { adjacent, cellOf, gridOf, indexOf } from '../neurons/grid';

describe('neuron grid', () => {
  it('lays Qwen3-4B ffn 9728 out as 99×99 with a partial last row', () => {
    const g = gridOf(9728);
    expect(g).toEqual({ ffn: 9728, cols: 99, rows: 99 });
    expect(cellOf(9727, g)).toEqual({ row: 98, col: 25 });
    expect(indexOf(98, 25, g)).toBe(9727);
    expect(indexOf(98, 26, g)).toBeNull();
  });

  it('round-trips every index through cellOf/indexOf', () => {
    for (const ffn of [1, 2, 7, 100, 9728]) {
      const g = gridOf(ffn);
      expect(g.cols * g.rows).toBeGreaterThanOrEqual(ffn);
      expect(g.cols * (g.rows - 1)).toBeLessThan(ffn);
      for (let i = 0; i < ffn; i++) {
        const { row, col } = cellOf(i, g);
        expect(indexOf(row, col, g)).toBe(i);
      }
    }
  });

  it('rejects off-grid cells and bad ffn', () => {
    const g = gridOf(10);
    expect(indexOf(-1, 0, g)).toBeNull();
    expect(indexOf(0, g.cols, g)).toBeNull();
    expect(indexOf(g.rows, 0, g)).toBeNull();
    expect(indexOf(0.5, 0, g)).toBeNull();
    expect(() => gridOf(0)).toThrow();
  });

  it('moves to adjacent cells, clamped at the edges and never past ffn-1', () => {
    const g = gridOf(9728);
    expect(adjacent(0, -1, -1, g)).toBe(0);
    expect(adjacent(0, 1, 0, g)).toBe(1);
    expect(adjacent(0, 0, 1, g)).toBe(99);
    expect(adjacent(98, 1, 0, g)).toBe(98);
    // Row 97 col 50 → down lands in the empty tail of the last row.
    expect(adjacent(97 * 99 + 50, 0, 1, g)).toBe(9727);
    expect(adjacent(9727, 5, 5, g)).toBe(9727);
    expect(adjacent(9727, 0, -1, g)).toBe(9727 - 99);
  });
});
