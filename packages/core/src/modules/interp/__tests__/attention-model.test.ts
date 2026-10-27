/**
 * The Attention section's drawing arithmetic. The layout itself is the backend's
 * (and tested there against real headers); these pin what the section draws from
 * it — chiefly that each kind's mask thumbnail allows exactly the keys that kind
 * allows, since a thumbnail is the one place the difference is meant to be seen.
 */
import { describe, expect, it } from 'vitest';

import type { LayerAttention } from '../../interpretability/store';
import { fmtBytes, fmtContext, kindMeta, maskCells, savings, shareArcs } from '../attention/model';

const allowed = (cells: boolean[][]) =>
  cells.flatMap((row, q) => row.map((on, k) => (on ? `${q}:${k}` : null))).filter(Boolean);

describe('maskCells', () => {
  it('global is the full causal triangle', () => {
    const cells = maskCells('global', 4)!;
    expect(allowed(cells)).toHaveLength(10);
    expect(cells[0][1]).toBe(false); // never a future key
  });

  it('sliding keeps only the last band of keys', () => {
    const cells = maskCells('sliding', 9)!; // band = 3
    expect(cells[8].map((on, k) => (on ? k : -1)).filter((k) => k >= 0)).toEqual([6, 7, 8]);
    expect(cells[2].filter(Boolean)).toHaveLength(3);
  });

  it('chunked never crosses a chunk boundary', () => {
    const cells = maskCells('chunked', 9)!; // chunks of 3
    expect(cells[3][2]).toBe(false);
    expect(cells[5].map((on, k) => (on ? k : -1)).filter((k) => k >= 0)).toEqual([3, 4, 5]);
  });

  it('recurrent kinds have no mask to draw', () => {
    expect(maskCells('linear', 6)).toBeNull();
    expect(maskCells('ssm', 6)).toBeNull();
  });
});

describe('formatting', () => {
  it('reads bytes and contexts the way the charts label them', () => {
    expect(fmtBytes(512)).toBe('512 B');
    expect(fmtBytes(774 * 1024 ** 2)).toBe('774 MB');
    expect(fmtBytes(5.25 * 1024 ** 3)).toBe('5.25 GB');
    expect(fmtContext(131072)).toBe('128k');
    expect(fmtContext(128000)).toBe('128k');
  });

  it('reports savings only when both sides are known', () => {
    expect(savings({ bytes: 100, bytesIfGlobal: 700 })).toBe(7);
    expect(savings({ bytes: 0, bytesIfGlobal: 700 })).toBeNull();
  });

  it('falls back to "none" for a kind it has never heard of', () => {
    expect(kindMeta('quantum').short).toBe('none');
  });
});

describe('shareArcs', () => {
  it('draws one arc per layer that reads another layer cache', () => {
    const layer = (n: number, src: number | null) =>
      ({ layer: n, kvSourceLayer: src }) as LayerAttention;
    expect(shareArcs([layer(0, null), layer(1, null), layer(2, 0), layer(3, 1)])).toEqual([
      { from: 0, to: 2 },
      { from: 1, to: 3 },
    ]);
  });
});
