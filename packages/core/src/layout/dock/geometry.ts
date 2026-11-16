/**
 * Pixel geometry of the workbench, derived from the frame and the surface size.
 *
 * The frame stores no rects: centre splits are fractions and docks are a pixel
 * extent, so there is nothing to go stale, rescale or land off-screen. Everything
 * that needs a rect (drop targets, the share mirror, the agent's `get_layout`,
 * the native browser view's "am I covered" check) asks here.
 */
import { MIN_DOCK_SIZE } from './frame';
import { collectAreas, computeRects } from './tree';
import type { DockFrame, DockSide, Rect } from './types';

export interface Surface {
  w: number;
  h: number;
}

/** A side dock may take at most this share of the surface's width. */
const MAX_SIDE_SHARE = 0.45;
/** The bottom dock may take at most this share of the surface's height. */
const MAX_BOTTOM_SHARE = 0.6;

export interface WorkbenchLayout {
  /** The centre tree's box: what is left once the visible docks are taken out. */
  center: Rect;
  /** Visible docks only. */
  docks: Partial<Record<DockSide, Rect>>;
  /** Every laid-out group in the main window, keyed by area id. */
  areas: Map<string, Rect>;
  /**
   * Groups hidden behind a maximized one. They keep their laid-out rect (they
   * stay mounted and sized, never `display: none`), so anything that asks
   * "is this on screen" must consult this rather than the rect.
   */
  covered: Set<string>;
}

/**
 * The extent a dock actually draws at. The stored size is the user's wish; a
 * narrow window cannot always grant it, and clamping here instead of in the
 * store means the dock returns to its wished size when the window grows back.
 */
export function dockExtent(side: DockSide, size: number, surface: Surface): number {
  const cap = Math.floor(
    side === 'bottom' ? surface.h * MAX_BOTTOM_SHARE : surface.w * MAX_SIDE_SHARE,
  );
  return Math.max(Math.min(size, cap), Math.min(MIN_DOCK_SIZE, cap));
}

/**
 * Lay the workbench out on `surface`. Side docks run the full height, the bottom
 * dock sits between them, and the centre gets what is left.
 */
export function computeWorkbench(frame: DockFrame, surface: Surface): WorkbenchLayout {
  const extent = (side: DockSide): number => {
    const dock = frame.docks[side];
    return dock.visible && dock.area.tabs.length > 0 ? dockExtent(side, dock.size, surface) : 0;
  };
  const left = extent('left');
  const right = extent('right');
  const bottom = extent('bottom');
  const midW = Math.max(surface.w - left - right, 0);

  const docks: WorkbenchLayout['docks'] = {};
  if (left) docks.left = { x: 0, y: 0, w: left, h: surface.h };
  if (right) docks.right = { x: surface.w - right, y: 0, w: right, h: surface.h };
  if (bottom) docks.bottom = { x: left, y: surface.h - bottom, w: midW, h: bottom };

  const center: Rect = { x: left, y: 0, w: midW, h: Math.max(surface.h - bottom, 0) };
  const tree = computeRects(frame.center, center);
  const areas = new Map<string, Rect>();
  for (const area of collectAreas(frame.center)) areas.set(area.id, tree.get(area.id)!);
  for (const side of Object.keys(docks) as DockSide[]) {
    areas.set(frame.docks[side].area.id, docks[side]!);
  }

  const covered = new Set<string>();
  const max = frame.maximizedAreaId;
  if (max && areas.has(max)) {
    for (const id of areas.keys()) if (id !== max) covered.add(id);
    areas.set(max, { x: 0, y: 0, w: surface.w, h: surface.h });
  }
  return { center, docks, areas, covered };
}

/** The id of the uncovered group under `point`, or null. */
export function areaAt(layout: WorkbenchLayout, point: { x: number; y: number }): string | null {
  for (const [id, r] of layout.areas) {
    if (layout.covered.has(id)) continue;
    if (point.x >= r.x && point.x < r.x + r.w && point.y >= r.y && point.y < r.y + r.h) return id;
  }
  return null;
}
