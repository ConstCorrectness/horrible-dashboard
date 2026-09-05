import { describe, expect, it } from 'vitest';

import {
  arrangeWindows,
  cascadeRect,
  clampRect,
  complementZone,
  DEFAULT_SNAP,
  fillTarget,
  isSnapZone,
  MIN_WINDOW_SIZE,
  rectForZone,
  overlapFraction,
  rescaleRect,
  SNAP_ZONES,
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

  it('tiles the surface exactly with thirds, at any width', () => {
    // Same invariant as the halves one width down: the last cell absorbs whatever
    // the first two floored away, so three thirds always sum to the full extent
    // rather than leaving a seam a window can never cover.
    for (const w of [1000, 1001, 1002, 1003, 999]) {
      const view = { w, h: 800 };
      const [l, c, r] = (['third-l', 'third-c', 'third-r'] as const).map((z) =>
        rectForZone(z, view),
      );
      expect(l.x).toBe(0);
      expect(l.x + l.w).toBe(c.x);
      expect(c.x + c.w).toBe(r.x);
      expect(r.x + r.w).toBe(w);
      // Full height, all three.
      expect([l.h, c.h, r.h]).toEqual([800, 800, 800]);
    }
  });

  it('centres the center zone at two thirds of each axis', () => {
    const c = rectForZone('center', VIEW);
    expect(c.w).toBe(667);
    expect(c.h).toBe(533);
    // Equal margins either side, to the pixel available: rects are integers, so
    // an odd remainder cannot split evenly and one side keeps the extra pixel.
    // Anything looser than ±1 is a box that sits visibly off-centre, which is the
    // only way this zone can be wrong and still look plausible.
    expect(Math.abs(c.x - (VIEW.w - (c.x + c.w)))).toBeLessThanOrEqual(1);
    expect(Math.abs(c.y - (VIEW.h - (c.y + c.h)))).toBeLessThanOrEqual(1);
  });

  it('never shrinks the center zone below a usable window', () => {
    // Two thirds of a tiny surface is smaller than the chrome needs. `center` is
    // the "read this one" zone, so it stops shrinking rather than producing
    // something that cannot be dragged back out.
    const tiny = { w: 300, h: 180 };
    const c = rectForZone('center', tiny);
    expect(c.w).toBeGreaterThanOrEqual(Math.min(MIN_WINDOW_SIZE.w, tiny.w));
    expect(c.h).toBeGreaterThanOrEqual(Math.min(MIN_WINDOW_SIZE.h, tiny.h));
    expect(c.w).toBeLessThanOrEqual(tiny.w);
    expect(c.h).toBeLessThanOrEqual(tiny.h);
  });

  it('has a rect for every declared zone', () => {
    // `SNAP_ZONES` is what `serialize`, `window-placement` and the agent tool all
    // filter against. A zone in that list with no case here would round-trip
    // through persistence and then land nowhere.
    for (const zone of SNAP_ZONES) {
      const r = rectForZone(zone, VIEW);
      expect(Number.isFinite(r.x + r.y + r.w + r.h)).toBe(true);
      expect(r.w).toBeGreaterThan(0);
      expect(r.h).toBeGreaterThan(0);
    }
  });
});

describe('isSnapZone', () => {
  it('accepts every declared zone and nothing else', () => {
    for (const zone of SNAP_ZONES) expect(isSnapZone(zone)).toBe(true);
    for (const bad of ['', 'middle', 'tl ', 'TL', null, undefined, 3, {}]) {
      expect(isSnapZone(bad)).toBe(false);
    }
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

describe('complementZone', () => {
  it('pairs the four halves', () => {
    expect(complementZone('left')).toBe('right');
    expect(complementZone('right')).toBe('left');
    expect(complementZone('top')).toBe('bottom');
    expect(complementZone('bottom')).toBe('top');
  });

  it('has none for zones whose remainder no single zone describes', () => {
    // A quarter leaves an L, a third leaves two, `center` leaves a frame and
    // `max` leaves nothing. Returning a "closest" zone would move a second window
    // somewhere the user never pointed.
    for (const zone of ['tl', 'tr', 'bl', 'br', 'third-l', 'third-c', 'third-r', 'center', 'max'] as const) {
      expect(complementZone(zone)).toBeNull();
    }
  });

  it('is an involution on every zone that has one', () => {
    for (const zone of SNAP_ZONES) {
      const other = complementZone(zone);
      if (other) expect(complementZone(other)).toBe(zone);
    }
  });

  it('tiles the surface with the rects of a complementary pair', () => {
    const l = rectForZone('left', VIEW);
    const r = rectForZone(complementZone('left')!, VIEW);
    expect(l.w + r.w).toBe(VIEW.w);
    expect(r.x).toBe(l.w);
  });
});

describe('overlapFraction', () => {
  const box: WindowRect = { x: 0, y: 0, w: 100, h: 100 };

  it('is 1 for an identical rect and 0 for a disjoint one', () => {
    expect(overlapFraction(box, box)).toBe(1);
    expect(overlapFraction(box, { x: 200, y: 0, w: 100, h: 100 })).toBe(0);
  });

  it('is a fraction of the FIRST rect, not of the intersection', () => {
    // Asymmetric on purpose: "how much of this window is now covered" is the
    // question, and a big window half-covering a small one is not the same fact
    // as a small one sitting inside a big one.
    const half = { x: 0, y: 0, w: 50, h: 100 };
    expect(overlapFraction(box, half)).toBeCloseTo(0.5);
    expect(overlapFraction(half, box)).toBe(1);
  });

  it('is 0 for an empty rect rather than dividing by zero', () => {
    expect(overlapFraction({ x: 0, y: 0, w: 0, h: 0 }, box)).toBe(0);
  });
});

describe('fillTarget', () => {
  const maxed = (id: string): WindowState => ({
    ...win(id, rectForZone('max', VIEW)),
    mode: 'maximized',
  });

  it('picks a maximized window to fill the other half', () => {
    const a = maxed('a');
    expect(fillTarget([a, win('b', rectForZone('right', VIEW))], 'b', 'right', VIEW)?.id).toBe('a');
  });

  it('is null when nothing covers the half being taken', () => {
    const away = win('a', { x: 600, y: 400, w: 300, h: 300 });
    expect(fillTarget([away, win('b', rectForZone('left', VIEW))], 'b', 'left', VIEW)).toBeNull();
  });

  it('is null for a zone with no complement, however covered', () => {
    expect(fillTarget([maxed('a')], 'b', 'tl', VIEW)).toBeNull();
    expect(fillTarget([maxed('a')], 'b', 'max', VIEW)).toBeNull();
  });

  it('skips a window already in the complement', () => {
    // Re-snapping it would overwrite the restoreRect it holds for its own un-snap.
    const settled: WindowState = { ...win('a', rectForZone('left', VIEW)), snap: 'left' };
    expect(fillTarget([settled], 'b', 'right', VIEW)).toBeNull();
  });

  it('skips a minimized window, which is not covering anything', () => {
    const hidden: WindowState = { ...maxed('a'), mode: 'minimized' };
    expect(fillTarget([hidden], 'b', 'right', VIEW)).toBeNull();
  });

  it('picks the topmost of several candidates', () => {
    const low = { ...maxed('low'), z: 1 };
    const high = { ...maxed('high'), z: 9 };
    expect(fillTarget([low, high], 'b', 'left', VIEW)?.id).toBe('high');
  });

  it('never picks the window being snapped', () => {
    expect(fillTarget([maxed('a')], 'a', 'left', VIEW)).toBeNull();
  });
});
