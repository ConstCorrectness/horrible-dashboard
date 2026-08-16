/**
 * The tiling ⇄ floating conversions behind `SET_DESKTOP_MODE`.
 *
 * Pure, registry-free and DOM-free like the rest of this directory, which is what
 * makes the mode switch testable without rendering anything — it is the one
 * operation in the desktop shell that rearranges every pane at once, so it is also
 * the one most worth pinning with tests.
 *
 * **The round trip is not lossless, and pretending otherwise would be the bug.**
 * Going to floating discards nothing but cannot express split ratios as anything but
 * the rects they currently occupy; coming back tiles by z-order and cannot recover
 * the ratios the user had dragged. What *is* preserved in both directions, and what
 * the tests assert, is the set of open pane instances: no pane is ever dropped, and
 * none is duplicated. Everything else is a best effort at visual continuity.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import {
  areaId,
  collectAreas,
  computeRects,
  createArea,
  createDock,
  firstArea,
  insertPane,
  normalize,
  splitArea,
  windowId,
} from './model';
import { clampRect } from './snap';
import type { AreaNode, DockSide, FrameState, LayoutNode, PaneState, WindowState } from './types';

/** Viewport assumed when nothing has measured the surface yet (the Tauri default). */
export const NOMINAL_VIEWPORT = { w: 1280, h: 800 };

/**
 * tiling → floating.
 *
 * One window per center **area** — carrying its tabs whole, so a tabbed area becomes
 * a tabbed window — positioned at the pixel rect that area occupies right now, so the
 * flip is visually continuous rather than a pile of cascaded boxes.
 *
 * Docked tools become windows too: a *visible* one at the dock's own geometry, a
 * hidden one **minimized**, which is the honest analogue — it stays mounted and
 * reachable from the taskbar, exactly as it was mounted and reachable from the rail.
 */
export function explodeToWindows(
  frame: FrameState,
  viewport: { w: number; h: number },
): FrameState {
  const surface = viewport.w > 0 && viewport.h > 0 ? viewport : NOMINAL_VIEWPORT;
  const rects = computeRects(frame.center);
  let seq = frame.paneSeq;
  const windows: WindowState[] = [...frame.windows];
  let z = windows.length;

  for (const area of collectAreas(frame.center)) {
    if (area.tabs.length === 0) continue;
    const unit = rects.get(area.id);
    const rect = unit
      ? {
          x: Math.round(unit.x * surface.w),
          y: Math.round(unit.y * surface.h),
          w: Math.round(unit.w * surface.w),
          h: Math.round(unit.h * surface.h),
        }
      : { x: 0, y: 0, w: surface.w, h: surface.h };
    windows.push({
      id: windowId(seq++),
      // A fresh id: the area is leaving the center tree, and reusing its id would
      // make `findAreaAnywhere` ambiguous for as long as both existed.
      area: { ...area, id: areaId(seq++) },
      rect: clampRect(rect, surface),
      mode: 'normal',
      z: ++z,
    });
  }

  for (const side of ['left', 'right', 'bottom'] as DockSide[]) {
    const dock = frame.docks[side];
    for (const tool of dock.tools) {
      const showing = dock.visible && dock.activeTool === tool.instanceId;
      windows.push({
        id: windowId(seq++),
        area: { kind: 'area', id: areaId(seq++), tabs: [tool], activeTab: 0 },
        rect: clampRect(dockRect(side, dock.size, tool.dockSize, surface), surface),
        mode: showing ? 'normal' : 'minimized',
        z: ++z,
      });
    }
  }

  const empty = createArea(areaId(seq++));
  return {
    ...frame,
    // The center tree collapses to one empty area and the docks empty out — their
    // contents are now windows. `tileWindows` rebuilds a tree from scratch, so
    // nothing here needs to be kept for the trip back.
    center: empty,
    docks: { left: createDock('left'), right: createDock('right'), bottom: createDock('bottom') },
    windows,
    windowViewport: surface,
    focusedAreaId: empty.id,
    fullscreenAreaId: null,
    paneSeq: seq,
  };
}

function dockRect(
  side: DockSide,
  dockSize: number,
  toolSize: number | undefined,
  surface: { w: number; h: number },
) {
  const size = toolSize ?? dockSize;
  if (side === 'bottom') return { x: 0, y: surface.h - size, w: surface.w, h: size };
  if (side === 'left') return { x: 0, y: 0, w: size, h: surface.h };
  return { x: surface.w - size, y: 0, w: size, h: surface.h };
}

/**
 * floating → tiling.
 *
 * Windows return to the center tree in z-order (back to front, so the window that was
 * on top ends up last and rightmost — the reading order the user just had). Each
 * window's area is carried across whole, so a tabbed window becomes a tabbed area.
 *
 * Two placements are deliberate rather than obvious:
 * - a pane whose view belongs in a dock (`dockFor`) goes back to that dock instead of
 *   into the grid, or a tiling desktop would come back with its file tree and terminal
 *   sitting in the document area;
 * - a **minimized** window's panes become background tabs of the first area — mounted
 *   but not showing, which is what minimized meant.
 */
export function tileWindows(frame: FrameState, dockFor: Record<string, DockSide>): FrameState {
  if (frame.windows.length === 0) return frame;
  const ordered = [...frame.windows].sort((a, b) => a.z - b.z);

  let center: LayoutNode = frame.center;
  let seq = frame.paneSeq;
  const docks = {
    left: { ...frame.docks.left, tools: [...frame.docks.left.tools] },
    right: { ...frame.docks.right, tools: [...frame.docks.right.tools] },
    bottom: { ...frame.docks.bottom, tools: [...frame.docks.bottom.tools] },
  };

  const toDock = (pane: PaneState, side: DockSide): void => {
    docks[side].tools.push(pane);
    docks[side].activeTool ??= pane.instanceId;
  };

  /** Tabs that had no window of their own to occupy — see the minimized rule above. */
  const background: PaneState[] = [];
  /** Areas to place, each already holding its tabs. */
  const placements: AreaNode[] = [];

  for (const win of ordered) {
    const tabs: PaneState[] = [];
    for (const tab of win.area.tabs) {
      const side = dockFor[tab.viewId];
      if (side) toDock(tab, side);
      else if (win.mode === 'minimized') background.push(tab);
      else tabs.push(tab);
    }
    if (tabs.length) {
      placements.push({ ...win.area, id: areaId(seq++), tabs, activeTab: 0 });
    }
  }

  // Place the first area's tabs into whatever empty area the center already has,
  // then split for each subsequent one, alternating orientation so the result is a
  // readable grid rather than N slivers in a row.
  for (let i = 0; i < placements.length; i++) {
    const target = i === 0 ? firstArea(center) : null;
    if (target && target.tabs.length === 0) {
      for (const tab of placements[i].tabs) {
        const next = insertPane(center, target.id, tab, { activate: false });
        if (next) center = next;
      }
      continue;
    }
    const host = lastArea(center);
    const res = splitArea(center, host.id, i % 2 === 1 ? 'below' : 'right', seq);
    if (!res) continue;
    center = res.root;
    seq = res.seq;
    for (const tab of placements[i].tabs) {
      const next = insertPane(center, res.newAreaId, tab, { activate: false });
      if (next) center = next;
    }
  }

  for (const tab of background) {
    const next = insertPane(center, firstArea(center).id, tab, { activate: false });
    if (next) center = next;
  }

  const normalized = normalize(center);
  return {
    ...frame,
    center: normalized,
    docks,
    windows: [],
    focusedAreaId: firstArea(normalized).id,
    focusedWindowId: null,
    paneSeq: seq,
  };
}

/** The last area in tree order — the one a fresh split should hang off. */
function lastArea(root: LayoutNode): AreaNode {
  const areas = collectAreas(root);
  return areas[areas.length - 1];
}
