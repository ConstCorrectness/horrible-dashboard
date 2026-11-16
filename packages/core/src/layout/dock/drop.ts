/**
 * Where a dragged tab would land.
 *
 * Pure: the gesture layer in packages/ui supplies a pointer position and draws
 * the `preview` rect; the decision is made here so the highlight and the drop
 * can never disagree, and so it can be tested without a DOM.
 */
import { findAreaAnywhere } from './frame';
import { areaAt } from './geometry';
import type { Surface, WorkbenchLayout } from './geometry';
import { dockExtent } from './geometry';
import type { DockFrame, DockSide, PlaceTarget, Rect, SplitSide } from './types';

/** `tabs` joins the group; a side splits it. */
export type DropZone = 'tabs' | SplitSide;

export type DropTarget =
  | { kind: 'area'; areaId: string; zone: DropZone; preview: Rect }
  | { kind: 'dock'; side: DockSide; preview: Rect };

/** The outer share of a group, on each side, that means "split here". */
const EDGE_BAND = 0.25;
/** How close to a workbench edge the pointer must be to offer a closed dock. */
const DOCK_REVEAL_PX = 24;

/**
 * Which part of a group the pointer is over. The nearest edge wins inside its
 * band, so a corner resolves to whichever edge the pointer is closer to instead
 * of to a fifth "corner" outcome the user would have to learn.
 */
export function dropZoneAt(point: { x: number; y: number }, rect: Rect): DropZone {
  if (rect.w <= 0 || rect.h <= 0) return 'tabs';
  const fx = (point.x - rect.x) / rect.w;
  const fy = (point.y - rect.y) / rect.h;
  const edges: [SplitSide, number][] = [
    ['left', fx],
    ['right', 1 - fx],
    ['above', fy],
    ['below', 1 - fy],
  ];
  const [side, distance] = edges.reduce((a, b) => (b[1] < a[1] ? b : a));
  return distance < EDGE_BAND ? side : 'tabs';
}

function half(rect: Rect, side: SplitSide): Rect {
  switch (side) {
    case 'left':
      return { ...rect, w: rect.w / 2 };
    case 'right':
      return { ...rect, x: rect.x + rect.w / 2, w: rect.w / 2 };
    case 'above':
      return { ...rect, h: rect.h / 2 };
    case 'below':
      return { ...rect, y: rect.y + rect.h / 2, h: rect.h / 2 };
  }
}

/**
 * Resolve a pointer position (in surface pixels) to a drop target.
 *
 * A closed dock is offered from a thin band along its edge of the workbench,
 * checked first: otherwise the centre group under the pointer would claim that
 * strip as its own split zone and a closed dock could never be dragged into.
 * A dock or an empty group only ever takes tabs; there is nothing to split.
 */
export function resolveDrop(
  frame: DockFrame,
  layout: WorkbenchLayout,
  surface: Surface,
  point: { x: number; y: number },
): DropTarget | null {
  if (point.x < 0 || point.y < 0 || point.x > surface.w || point.y > surface.h) return null;

  if (!frame.maximizedAreaId) {
    const closed = (side: DockSide) => !layout.docks[side];
    const size = (side: DockSide) => dockExtent(side, frame.docks[side].size, surface);
    if (closed('left') && point.x < DOCK_REVEAL_PX) {
      return { kind: 'dock', side: 'left', preview: { x: 0, y: 0, w: size('left'), h: surface.h } };
    }
    if (closed('right') && point.x > surface.w - DOCK_REVEAL_PX) {
      const w = size('right');
      return { kind: 'dock', side: 'right', preview: { x: surface.w - w, y: 0, w, h: surface.h } };
    }
    if (closed('bottom') && point.y > surface.h - DOCK_REVEAL_PX) {
      const h = size('bottom');
      return {
        kind: 'dock',
        side: 'bottom',
        preview: { x: layout.center.x, y: surface.h - h, w: layout.center.w, h },
      };
    }
  }

  const areaId = areaAt(layout, point);
  if (!areaId) return null;
  const rect = layout.areas.get(areaId)!;
  const found = findAreaAnywhere(frame, areaId);
  if (!found) return null;
  const splittable =
    found.host.kind === 'center' && found.area.tabs.length > 0 && !frame.maximizedAreaId;
  const zone = splittable ? dropZoneAt(point, rect) : 'tabs';
  return { kind: 'area', areaId, zone, preview: zone === 'tabs' ? rect : half(rect, zone) };
}

/** The `PlaceTarget` a drop target stands for, for `movePane` / `insertPane`. */
export function dropToPlace(target: DropTarget): PlaceTarget {
  if (target.kind === 'dock') return { kind: 'dock', side: target.side };
  return target.zone === 'tabs'
    ? { kind: 'area', areaId: target.areaId }
    : { kind: 'split', areaId: target.areaId, side: target.zone };
}
