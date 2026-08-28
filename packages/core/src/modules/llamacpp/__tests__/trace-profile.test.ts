import { describe, expect, it } from 'vitest';

import type { ProfilePoint } from '../api';
import { profilableRoles, profileByLayer, roleGrid, roleOf } from '../trace-profile';

function point(over: Partial<ProfilePoint>): ProfilePoint {
  return { index: 0, name: 'l_out-0', layer: 0, value: 1, fidelity: 'fp16', ...over };
}

/** A stack of `l_out-N` residuals whose value grows with depth. */
function residuals(layers: number): ProfilePoint[] {
  return Array.from({ length: layers }, (_, i) =>
    point({ index: i, name: `l_out-${i}`, layer: i, value: 1 + i }),
  );
}

describe('roleOf', () => {
  /* The bug this pins: llama.cpp's `cb()` names carry the block index as a `-N`
     SUFFIX, and a rule that only knew `blk.N.` left every activation name
     unchanged — so each depth read as its own role and nothing was profilable. */
  it('strips a suffix block index so two depths are the same role', () => {
    expect(roleOf('l_out-17')).toBe(roleOf('l_out-3'));
    expect(roleOf('l_out-17')).toBe('l_out');
    expect(roleOf('kqv_out-27')).toBe('kqv_out');
  });

  it('strips a segment block index too', () => {
    expect(roleOf('blk.17.attn_out')).toBe(roleOf('blk.3.attn_out'));
    expect(roleOf('blk.17.attn_out')).toBe('attn_out');
  });

  it('leaves a name with no block index alone', () => {
    expect(roleOf('inp_embd')).toBe('inp_embd');
    expect(roleOf('result_output')).toBe('result_output');
  });
});

describe('profilableRoles', () => {
  it('offers only roles that appear at more than one depth', () => {
    const points = [
      ...residuals(4),
      point({ index: 99, name: 'inp_embd', layer: null }),
      point({ index: 98, name: 'once-0', layer: 0 }),
    ];
    const roles = profilableRoles(points);
    expect(roles).toContain('l_out');
    expect(roles).not.toContain('inp_embd');
    expect(roles).not.toContain('once');
  });

  it('orders the commonest role first', () => {
    const points = [...residuals(4), point({ index: 50, name: 'ffn_out-0', layer: 0 })];
    points.push(point({ index: 51, name: 'ffn_out-1', layer: 1 }));
    expect(profilableRoles(points)[0]).toBe('l_out');
  });
});

describe('profileByLayer', () => {
  it('returns one point per layer, in depth order', () => {
    const shuffled = [...residuals(4)].reverse();
    const points = profileByLayer(shuffled, 'l_out');
    expect(points.map((p) => p.layer)).toEqual([0, 1, 2, 3]);
    expect(points.map((p) => p.value)).toEqual([1, 2, 3, 4]);
  });

  it('leaves out other roles and the layerless nodes', () => {
    const points = [
      ...residuals(2),
      point({ index: 9, name: 'ffn_out-0', layer: 0 }),
      point({ index: 8, name: 'l_out', layer: null }),
    ];
    expect(profileByLayer(points, 'l_out').map((p) => p.index)).toEqual([0, 1]);
  });

  /* THE rule. The route reports an unmeasurable record as null, and it must stay
     null: zero is a measurement, and drawing one that was never taken would drag
     every scale toward it. */
  it('passes a null through rather than turning it into a zero', () => {
    const points = [
      point({ index: 0, name: 'l_out-0', layer: 0, value: 5 }),
      point({ index: 1, name: 'l_out-1', layer: 1, value: null, fidelity: 'summary' }),
    ];
    expect(profileByLayer(points, 'l_out').map((p) => p.value)).toEqual([5, null]);
  });

  /* The other half of the same rule: a genuine 0.0 must survive as a measurement. */
  it('keeps a real zero', () => {
    const points = [point({ index: 0, name: 'l_out-0', layer: 0, value: 0 })];
    expect(profileByLayer(points, 'l_out')[0].value).toBe(0);
  });
});

describe('roleGrid', () => {
  it('has a row per node kind and a column per layer', () => {
    const grid = roleGrid(residuals(4));
    expect(grid.layers).toEqual([0, 1, 2, 3]);
    expect(grid.cells).toHaveLength(grid.kinds.length);
    expect(grid.cells[0]).toHaveLength(4);
  });

  /* Per-ROW normalization, not global. An attention score and an FFN activation
     are different quantities in different units; one shared scale would render
     every row but the largest as blank, which reads as "nothing happens there". */
  it('scales each row against its own maximum', () => {
    const points = [
      point({ index: 0, name: 'kqv_out-0', layer: 0, value: 1000 }),
      point({ index: 1, name: 'kqv_out-1', layer: 1, value: 500 }),
      point({ index: 2, name: 'ffn_out-0', layer: 0, value: 0.02 }),
      point({ index: 3, name: 'ffn_out-1', layer: 1, value: 0.01 }),
    ];
    const grid = roleGrid(points);
    // Both rows peak at 1 despite spanning five orders of magnitude between them.
    for (const row of grid.cells) expect(Math.max(...row.map((c) => c ?? 0))).toBeCloseTo(1);
  });

  it('averages several records of one kind landing on one layer', () => {
    const points = [
      point({ index: 0, name: 'q_cur-0', layer: 0, value: 2 }),
      point({ index: 1, name: 'k_cur-0', layer: 0, value: 4 }),
    ];
    const grid = roleGrid(points);
    expect(grid.raw[0][0]).toBe(3);
  });

  it('leaves a cell with no record as null rather than as zero', () => {
    const points = [
      point({ index: 0, name: 'kqv_out-0', layer: 0, value: 1 }),
      point({ index: 1, name: 'ffn_out-1', layer: 1, value: 1 }),
    ];
    const grid = roleGrid(points);
    const attnRow = grid.cells[grid.kinds.indexOf('attention')];
    expect(attnRow[grid.layers.indexOf(1)]).toBeNull();
  });

  it('keeps a real zero in the grid rather than dropping it as falsy', () => {
    const points = [
      point({ index: 0, name: 'ffn_out-0', layer: 0, value: 0 }),
      point({ index: 1, name: 'ffn_out-1', layer: 1, value: 4 }),
    ];
    const grid = roleGrid(points);
    expect(grid.raw[0][0]).toBe(0);
    expect(grid.cells[0][0]).toBe(0);
  });

  it('returns an empty grid rather than throwing when nothing was measured', () => {
    const grid = roleGrid([point({ index: 0, layer: 0, value: null })]);
    expect(grid.kinds).toEqual([]);
  });
});
