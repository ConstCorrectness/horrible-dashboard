import { describe, expect, it } from 'vitest';

import {
  arrangeWindows,
  cascadeRect,
  clampRect,
  DEFAULT_SNAP,
  MIN_WINDOW_SIZE,
  rectForZone,
  rescaleRect,
  snapZoneAt,
  TITLEBAR_KEEP,
} from '../snap';
import type { WindowRect, WindowState } from '../types';

const VIEW = { w: 1000, h: 800 };

function win(id: string, rect: WindowRect): WindowState {
  return {
    id,
    area: { kind: 'area', id: `a-${id}`, tabs: [], activeTab: 0 },
    rect,
    mode: 'normal',
    z: 1,
  };
}

describe('snapZoneAt', () => {
  it('arms halves on the edges and maximize on the top', () => {
    expect(snapZoneAt({ x: 2, y: 400 }, VIEW)).toBe('left');
    expect(snapZoneAt({ x: 998, y: 400 }, VIEW)).toBe('right');
    expect(snapZoneAt({ x: 500, y: 2 }, VIEW)).toBe('max');
    expect(snapZoneAt({ x: 500, y: 798 }, VIEW)).toBe('bottom');
  });

  it('prefers a corner over either edge that forms it', () => {
    // Inside a corner box the pointer is within `edge` of two sides at once; the
    // corner must win, or whichever edge test ran first would decide arbitrarily.
    expect(snapZoneAt({ x: 2, y: 2 }, VIEW)).toBe('tl');
    expect(snapZoneAt({ x: 998, y: 2 }, VIEW)).toBe('tr');
    expect(snapZoneAt({ x: 2, y: 798 }, VIEW)).toBe('bl');
    expect(snapZoneAt({ x: 998, y: 798 }, VIEW)).toBe('br');
  });

  it('reaches the corner along either arm, not just diagonally', () => {
    const nearCorner = DEFAULT_SNAP.corner - 4;
    expect(snapZoneAt({ x: 2, y: nearCorner }, VIEW)).toBe('tl');
    expect(snapZoneAt({ x: nearCorner, y: 2 }, VIEW)).toBe('tl');
  });

  it('is null well away from every edge', () => {
    expect(snapZoneAt({ x: 500, y: 400 }, VIEW)).toBeNull();
  });
});

describe('rectForZone', () => {
  it('tiles the surface exactly with halves', () => {
    const l = rectForZone('left', VIEW);
    const r = rectForZone('right', VIEW);
    expect(l.x).toBe(0);
    expect(l.x + l.w).toBe(r.x);
    expect(r.x + r.w).toBe(VIEW.w);
  });

  it('gives the odd pixel to the far half rather than overlapping or gapping', () => {
    const odd = { w: 1001, h: 801 };
    const l = rectForZone('left', odd);
    const r = rectForZone('right', odd);
    expect(l.w + r.w).toBe(odd.w);
    const t = rectForZone('top', odd);
    const b = rectForZone('bottom', odd);
    expect(t.h + b.h).toBe(odd.h);
  });

  it('tiles the surface exactly with quarters', () => {
    const quads = (['tl', 'tr', 'bl', 'br'] as const).map((z) => rectForZone(z, VIEW));
    const area = quads.reduce((sum, q) => sum + q.w * q.h, 0);
    expect(area).toBe(VIEW.w * VIEW.h);
  });

  it('max fills the surface', () => {
    expect(rectForZone('max', VIEW)).toEqual({ x: 0, y: 0, w: 1000, h: 800 });
  });
});

describe('rescaleRect', () => {
  it('scales proportionally on each axis', () => {
    const r = rescaleRect(
      { x: 100, y: 100, w: 400, h: 200 },
      { w: 1000, h: 800 },
      { w: 500, h: 400 },
    );
    expect(r).toEqual({ x: 50, y: 50, w: 200, h: 100 });
  });

  it('round-trips through a scale and back', () => {
    const original = { x: 120, y: 80, w: 400, h: 300 };
    const there = rescaleRect(original, { w: 1000, h: 800 }, { w: 2000, h: 1600 });
    expect(rescaleRect(there, { w: 2000, h: 1600 }, { w: 1000, h: 800 })).toEqual(original);
  });

  it('passes the rect through when the origin was never measured', () => {
    // A zero-sized basis carries nothing to scale by; multiplying would produce
    // Infinity and lose the layout entirely.
    const r = { x: 10, y: 10, w: 100, h: 100 };
    expect(rescaleRect(r, { w: 0, h: 0 }, VIEW)).toEqual(r);
  });
});

describe('clampRect', () => {
  it('keeps a titlebar reachable when dragged off the right', () => {
    const r = clampRect({ x: 5000, y: 100, w: 400, h: 300 }, VIEW);
    expect(r.x).toBeLessThanOrEqual(VIEW.w - TITLEBAR_KEEP.w);
    expect(r.x + r.w).toBeGreaterThan(VIEW.w - TITLEBAR_KEEP.w);
  });

  it('allows a window mostly off the left but keeps a grabbable strip', () => {
    const r = clampRect({ x: -5000, y: 100, w: 400, h: 300 }, VIEW);
    expect(r.x + r.w).toBeGreaterThanOrEqual(TITLEBAR_KEEP.w);
  });

  it('never allows a negative y', () => {
    // A window above the surface hides its own titlebar behind the app's title
    // strip, where dragging moves the OS window and the user cannot recover it.
    expect(clampRect({ x: 10, y: -300, w: 400, h: 300 }, VIEW).y).toBe(0);
  });

  it('enforces a minimum size', () => {
    const r = clampRect({ x: 10, y: 10, w: 5, h: 5 }, VIEW);
    expect(r.w).toBe(MIN_WINDOW_SIZE.w);
    expect(r.h).toBe(MIN_WINDOW_SIZE.h);
  });
});

describe('cascadeRect', () => {
  it('steps diagonally and stays on the surface', () => {
    const a = cascadeRect(0, VIEW);
    const b = cascadeRect(1, VIEW);
    expect(b.x).toBeGreaterThan(a.x);
    expect(b.y).toBeGreaterThan(a.y);
    for (let i = 0; i < 40; i++) {
      const r = cascadeRect(i, VIEW);
      expect(r.y).toBeGreaterThanOrEqual(0);
      expect(r.x).toBeLessThanOrEqual(VIEW.w - TITLEBAR_KEEP.w);
    }
  });

  it('wraps rather than piling every later window on one spot', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 12; i++) {
      const r = cascadeRect(i, VIEW);
      seen.add(`${r.x},${r.y}`);
    }
    expect(seen.size).toBeGreaterThan(1);
  });
});

describe('arrangeWindows', () => {
  const four = [
    win('w1', { x: 0, y: 0, w: 300, h: 200 }),
    win('w2', { x: 0, y: 0, w: 300, h: 200 }),
    win('w3', { x: 0, y: 0, w: 300, h: 200 }),
    win('w4', { x: 0, y: 0, w: 300, h: 200 }),
  ];

  it('grids four windows into two by two covering the surface', () => {
    const rects = arrangeWindows(four, VIEW, 'grid');
    expect(rects).toHaveLength(4);
    expect(rects.reduce((s, r) => s + r.w * r.h, 0)).toBe(VIEW.w * VIEW.h);
  });

  it('columns and rows lay out on one axis', () => {
    const cols = arrangeWindows(four, VIEW, 'columns');
    expect(new Set(cols.map((r) => r.y)).size).toBe(1);
    const rows = arrangeWindows(four, VIEW, 'rows');
    expect(new Set(rows.map((r) => r.x)).size).toBe(1);
  });

  it('returns nothing for no windows', () => {
    expect(arrangeWindows([], VIEW, 'grid')).toEqual([]);
  });
});
