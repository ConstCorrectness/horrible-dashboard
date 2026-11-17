/**
 * Pixel layout and drop targets. The property under test in both: a rect is
 * derived from the frame and the surface every time, so it cannot be stale and
 * cannot be off-screen.
 */
import { describe, expect, it } from 'vitest';

import type { PaneState } from '../../types';
import { dropToPlace, dropZoneAt, resolveDrop } from '../drop';
import { createEmptyDockFrame, insertPane, setDockSize, toggleMaximized } from '../frame';
import { areaAt, computeWorkbench } from '../geometry';
import { collectAreas } from '../tree';
import type { DockFrame, PlaceTarget } from '../types';

const pane = (id: string): PaneState => ({ instanceId: id, viewId: id.split('#')[0] });
const SURFACE = { w: 1600, h: 900 };

function build(...steps: [string, PlaceTarget][]): DockFrame {
  return steps.reduce(
    (frame, [id, target]) => insertPane(frame, pane(id), target)!,
    createEmptyDockFrame(),
  );
}

const full = build(
  ['doc#1', { kind: 'center' }],
  ['files#2', { kind: 'dock', side: 'left' }],
  ['chat#3', { kind: 'dock', side: 'right' }],
  ['term#4', { kind: 'dock', side: 'bottom' }],
);

describe('computeWorkbench', () => {
  it('tiles the surface with no gaps and no overlap', () => {
    const layout = computeWorkbench(full, SURFACE);
    const covered = [...layout.areas.values()].reduce((sum, r) => sum + r.w * r.h, 0);
    expect(covered).toBe(SURFACE.w * SURFACE.h);
    expect(layout.docks.left).toEqual({ x: 0, y: 0, w: 280, h: 900 });
    expect(layout.docks.right).toEqual({ x: 1280, y: 0, w: 320, h: 900 });
    // The bottom dock sits between the side docks.
    expect(layout.docks.bottom).toEqual({ x: 280, y: 660, w: 1000, h: 240 });
    expect(layout.center).toEqual({ x: 280, y: 0, w: 1000, h: 660 });
  });

  it('gives a hidden or empty dock no space', () => {
    const layout = computeWorkbench(build(['doc#1', { kind: 'center' }]), SURFACE);
    expect(layout.docks).toEqual({});
    expect(layout.center).toEqual({ x: 0, y: 0, w: 1600, h: 900 });
  });

  it('keeps every rect on the surface however small it gets', () => {
    const greedy = setDockSize(setDockSize(full, 'left', 2000), 'bottom', 2000);
    for (const surface of [SURFACE, { w: 640, h: 400 }, { w: 200, h: 120 }]) {
      const layout = computeWorkbench(greedy, surface);
      for (const r of layout.areas.values()) {
        expect(r.x).toBeGreaterThanOrEqual(0);
        expect(r.y).toBeGreaterThanOrEqual(0);
        expect(r.x + r.w).toBeLessThanOrEqual(surface.w);
        expect(r.y + r.h).toBeLessThanOrEqual(surface.h);
      }
      expect(layout.center.w).toBeGreaterThanOrEqual(0);
    }
  });

  it('a wished dock size survives a narrow window', () => {
    const wide = setDockSize(full, 'left', 600);
    expect(computeWorkbench(wide, { w: 800, h: 600 }).docks.left!.w).toBe(360);
    // Nothing was written back: the wish is still there when the window grows.
    expect(computeWorkbench(wide, SURFACE).docks.left!.w).toBe(600);
  });

  it('a maximized group takes the surface and marks the rest covered', () => {
    const id = full.docks.bottom.area.id;
    const layout = computeWorkbench(toggleMaximized(full, id), SURFACE);
    expect(layout.areas.get(id)).toEqual({ x: 0, y: 0, ...SURFACE });
    expect(layout.covered.size).toBe(layout.areas.size - 1);
    expect(areaAt(layout, { x: 10, y: 10 })).toBe(id);
  });
});

describe('dropZoneAt', () => {
  const rect = { x: 0, y: 0, w: 400, h: 400 };

  it('reads the middle as "join the tabs" and the bands as splits', () => {
    expect(dropZoneAt({ x: 200, y: 200 }, rect)).toBe('tabs');
    expect(dropZoneAt({ x: 20, y: 200 }, rect)).toBe('left');
    expect(dropZoneAt({ x: 380, y: 200 }, rect)).toBe('right');
    expect(dropZoneAt({ x: 200, y: 20 }, rect)).toBe('above');
    expect(dropZoneAt({ x: 200, y: 380 }, rect)).toBe('below');
  });

  it('resolves a corner to the nearer edge', () => {
    expect(dropZoneAt({ x: 10, y: 40 }, rect)).toBe('left');
    expect(dropZoneAt({ x: 40, y: 10 }, rect)).toBe('above');
  });
});

describe('resolveDrop', () => {
  it('offers a closed dock from a band along its edge', () => {
    const frame = build(['doc#1', { kind: 'center' }]);
    const layout = computeWorkbench(frame, SURFACE);
    const target = resolveDrop(frame, layout, SURFACE, { x: 800, y: 895 })!;
    expect(target).toMatchObject({ kind: 'dock', side: 'bottom' });
    expect(dropToPlace(target)).toEqual({ kind: 'dock', side: 'bottom' });
  });

  it('does not offer a dock that is already open: its own group is the target', () => {
    const layout = computeWorkbench(full, SURFACE);
    const target = resolveDrop(full, layout, SURFACE, { x: 800, y: 895 })!;
    expect(target).toMatchObject({ kind: 'area', areaId: full.docks.bottom.area.id, zone: 'tabs' });
  });

  it('never splits a dock', () => {
    const layout = computeWorkbench(full, SURFACE);
    // Deep in the left band of the left dock.
    const target = resolveDrop(full, layout, SURFACE, { x: 30, y: 450 })!;
    expect(target).toMatchObject({ kind: 'area', zone: 'tabs' });
  });

  it('splits a centre group from its edge band and previews the half it will take', () => {
    const layout = computeWorkbench(full, SURFACE);
    const home = collectAreas(full.center)[0].id;
    const target = resolveDrop(full, layout, SURFACE, { x: 1250, y: 300 })!;
    expect(target).toMatchObject({ kind: 'area', areaId: home, zone: 'right' });
    expect(target.preview).toEqual({ x: 780, y: 0, w: 500, h: 660 });
    expect(dropToPlace(target)).toEqual({ kind: 'split', areaId: home, side: 'right' });
  });

  it('an empty centre only takes tabs', () => {
    const frame = createEmptyDockFrame();
    const layout = computeWorkbench(frame, SURFACE);
    expect(resolveDrop(frame, layout, SURFACE, { x: 100, y: 450 })).toMatchObject({
      kind: 'area',
      zone: 'tabs',
    });
  });

  it('returns null off the surface', () => {
    const layout = computeWorkbench(full, SURFACE);
    expect(resolveDrop(full, layout, SURFACE, { x: -5, y: 10 })).toBeNull();
  });
});
