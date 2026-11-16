/**
 * Pure geometry the workbench draws from: where the sashes go, how a sash drag
 * turns into new sizes, and the frame as it looks mid-drag.
 *
 * Kept out of the components so the arithmetic can be tested without a DOM, and
 * so the drag preview and the committed result are computed by the same code.
 */
import {
  computeWorkbench,
  dockExtent,
  MIN_DOCK_SIZE,
  MIN_FRACTION,
  type DockFrame,
  type DockSide,
  type LayoutNode,
  type Surface,
  type WorkbenchLayout,
} from '@horrible/core';

/** Height of a group's tab strip, accent rule included. Matches `--control-h`. */
export const TAB_H = 30;

/** How far either side of a divider still grabs it. The line itself is 1px. */
export const SASH_GRAB = 3;

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A divider between two children of a centre split. */
export interface SplitSash {
  kind: 'split';
  key: string;
  splitId: string;
  /** The boundary between `children[index]` and `children[index + 1]`. */
  index: number;
  orientation: 'row' | 'column';
  /** The whole split's box, which the boundary is a fraction of. */
  container: Rect;
  sizes: number[];
  /** Where the 1px line is drawn. */
  line: Rect;
}

/** The inner edge of a visible dock. */
export interface DockSash {
  kind: 'dock';
  key: string;
  side: DockSide;
  line: Rect;
}

export type Sash = SplitSash | DockSash;

/** Live sizes from a sash drag, laid over the frame until pointer-up commits them. */
export interface LiveSizes {
  split?: { id: string; sizes: number[] };
  dock?: { side: DockSide; size: number };
}

/** `frame` with a drag's live sizes applied. Returns `frame` itself when there are none. */
export function withLiveSizes(frame: DockFrame, live: LiveSizes | null): DockFrame {
  if (!live || (!live.split && !live.dock)) return frame;
  let next = frame;
  if (live.split) {
    const { id, sizes } = live.split;
    const walk = (n: LayoutNode): LayoutNode =>
      n.kind === 'area'
        ? n
        : n.id === id
          ? { ...n, sizes }
          : { ...n, children: n.children.map(walk) };
    next = { ...next, center: walk(next.center) };
  }
  if (live.dock) {
    const { side, size } = live.dock;
    next = { ...next, docks: { ...next.docks, [side]: { ...next.docks[side], size } } };
  }
  return next;
}

/** Every divider in the workbench, centre splits and dock edges. */
export function sashesOf(frame: DockFrame, layout: WorkbenchLayout): Sash[] {
  const out: Sash[] = [];
  if (frame.maximizedAreaId) return out;
  const walk = (n: LayoutNode, rect: Rect): void => {
    if (n.kind === 'area') return;
    let offset = n.orientation === 'row' ? rect.x : rect.y;
    n.children.forEach((child, i) => {
      const frac = n.sizes[i];
      const childRect =
        n.orientation === 'row'
          ? { x: offset, y: rect.y, w: rect.w * frac, h: rect.h }
          : { x: rect.x, y: offset, w: rect.w, h: rect.h * frac };
      offset += n.orientation === 'row' ? childRect.w : childRect.h;
      walk(child, childRect);
      if (i === n.children.length - 1) return;
      out.push({
        kind: 'split',
        key: `${n.id}:${i}`,
        splitId: n.id,
        index: i,
        orientation: n.orientation,
        container: rect,
        sizes: n.sizes,
        line:
          n.orientation === 'row'
            ? { x: offset, y: rect.y, w: 0, h: rect.h }
            : { x: rect.x, y: offset, w: rect.w, h: 0 },
      });
    });
  };
  walk(frame.center, layout.center);
  const { left, right, bottom } = layout.docks;
  if (left)
    out.push({
      kind: 'dock',
      key: 'dock:left',
      side: 'left',
      line: { ...left, x: left.x + left.w, w: 0 },
    });
  if (right) out.push({ kind: 'dock', key: 'dock:right', side: 'right', line: { ...right, w: 0 } });
  if (bottom)
    out.push({ kind: 'dock', key: 'dock:bottom', side: 'bottom', line: { ...bottom, h: 0 } });
  return out;
}

/**
 * The sizes a split takes when its `index` boundary is dragged to `pos` (surface
 * pixels along the split's axis). Only the two children either side change, and
 * neither drops below `MIN_FRACTION` of the split.
 */
export function dragSplit(sash: SplitSash, pos: number): number[] {
  const { container, sizes, index, orientation } = sash;
  const start = orientation === 'row' ? container.x : container.y;
  const extent = orientation === 'row' ? container.w : container.h;
  if (extent <= 0) return sizes;
  const lo = sizes.slice(0, index).reduce((a, b) => a + b, 0);
  const hi = lo + sizes[index] + sizes[index + 1];
  const at = Math.min(Math.max((pos - start) / extent, lo + MIN_FRACTION), hi - MIN_FRACTION);
  const next = [...sizes];
  next[index] = at - lo;
  next[index + 1] = hi - at;
  return next;
}

/**
 * The size a dock takes when its inner edge is dragged to `point`. Clamped to
 * what `computeWorkbench` would actually draw, so the stored wish never sits
 * past a limit the user cannot see and then "sticks" there on the way back.
 */
export function dragDock(
  side: DockSide,
  point: { x: number; y: number },
  surface: Surface,
): number {
  const raw =
    side === 'left' ? point.x : side === 'right' ? surface.w - point.x : surface.h - point.y;
  return dockExtent(side, Math.max(Math.round(raw), MIN_DOCK_SIZE), surface);
}

/**
 * Where each group would sit if every dock were open. A hidden dock's pane stays
 * mounted (a terminal keeps its shell), and it keeps the size it had rather than
 * collapsing to nothing — xterm and canvases rarely recover from a zero box.
 */
export function shadowLayout(frame: DockFrame, surface: Surface): WorkbenchLayout {
  const open = {
    ...frame,
    maximizedAreaId: null,
    docks: {
      left: { ...frame.docks.left, visible: true },
      right: { ...frame.docks.right, visible: true },
      bottom: { ...frame.docks.bottom, visible: true },
    },
  };
  return computeWorkbench(open, surface);
}

/** A group's body: its rect less the tab strip. */
export function bodyOf(rect: Rect): Rect {
  return { x: rect.x, y: rect.y + TAB_H, w: rect.w, h: Math.max(rect.h - TAB_H, 0) };
}
