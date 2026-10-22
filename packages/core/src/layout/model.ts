/**
 * Pure operations on the desktop's windows and their tab lists. Every function is
 * immutable (returns new values, never mutates) and deterministic — id allocation
 * draws from the caller-supplied monotonic `seq` (`FrameState.paneSeq`), so a
 * caller holding a snapshot can precompute the ids an operation will assign. No
 * React, no registry, no DOM.
 */
import { DEFAULT_BACKDROP } from './types';
import type { AreaNode, FrameState, LocatedPane, PaneState, WindowState } from './types';

export function areaId(seq: number): string {
  return `a${seq}`;
}

export function windowId(seq: number): string {
  return `w${seq}`;
}

export function instanceId(viewId: string, seq: number): string {
  return `${viewId}#${seq}`;
}

export function createArea(id: string): AreaNode {
  return { kind: 'area', id, tabs: [], activeTab: 0 };
}

/** An empty desktop: no windows, the default backdrop. */
export function createEmptyFrame(): FrameState {
  return {
    windows: [],
    windowViewport: null,
    backdrop: { id: DEFAULT_BACKDROP },
    presentedInstanceId: null,
    focusedInstanceId: null,
    focusedWindowId: null,
    paneSeq: 1,
  };
}

// ---------------------------------------------------------------------------
// Tab edits (one window's area)
// ---------------------------------------------------------------------------

export function insertPane(
  area: AreaNode,
  pane: PaneState,
  opts: { activate?: boolean } = {},
): AreaNode {
  const tabs = [...area.tabs, pane];
  return {
    ...area,
    tabs,
    activeTab: opts.activate === false ? area.activeTab : tabs.length - 1,
  };
}

/** Null when `index` is out of range — the reducer's "no change" signal. */
export function setActiveTab(area: AreaNode, index: number): AreaNode | null {
  if (index < 0 || index >= area.tabs.length) return null;
  return { ...area, activeTab: index };
}

/**
 * Move one tab within its area, splice-style: the tab at `from` is pulled out and
 * re-inserted so it ends up at `to` in the *resulting* list.
 *
 * `activeTab` is carried by identity, not by index — it is a position, and every
 * naive reorder bug is the same one: the user drags a background tab past the
 * active one and the window silently switches to a different pane. Which pane is on
 * screen must not change because the strip was rearranged.
 */
export function reorderTab(area: AreaNode, from: number, to: number): AreaNode | null {
  const n = area.tabs.length;
  if (from < 0 || from >= n || to < 0 || to >= n || from === to) return null;
  const active = area.tabs[area.activeTab];
  const tabs = [...area.tabs];
  const [moved] = tabs.splice(from, 1);
  tabs.splice(to, 0, moved);
  const activeTab = active ? tabs.indexOf(active) : area.activeTab;
  return { ...area, tabs, activeTab: activeTab >= 0 ? activeTab : 0 };
}

// ---------------------------------------------------------------------------
// Frame-level lookups/edits
// ---------------------------------------------------------------------------

export function findWindow(frame: FrameState, id: string): WindowState | null {
  return frame.windows.find((w) => w.id === id) ?? null;
}

/** The window whose area holds `instanceId`, or null. */
export function windowOfInstance(frame: FrameState, instanceId: string): WindowState | null {
  return frame.windows.find((w) => w.area.tabs.some((t) => t.instanceId === instanceId)) ?? null;
}

/** Find a window's area by the area's id. */
export function findAreaAnywhere(
  frame: FrameState,
  id: string,
): { area: AreaNode; window: WindowState } | null {
  const win = frame.windows.find((w) => w.area.id === id);
  return win ? { area: win.area, window: win } : null;
}

/** Apply `fn` to a window's area, addressed by the area's id. Null if unknown. */
export function updateAreaAnywhere(
  frame: FrameState,
  id: string,
  fn: (area: AreaNode) => AreaNode,
): FrameState | null {
  if (!frame.windows.some((w) => w.area.id === id)) return null;
  return {
    ...frame,
    windows: frame.windows.map((w) => (w.area.id === id ? { ...w, area: fn(w.area) } : w)),
  };
}

function locate(win: WindowState, pane: PaneState): LocatedPane {
  return { pane, location: { kind: 'window', windowId: win.id, areaId: win.area.id } };
}

export function findPaneAnywhere(frame: FrameState, instanceId: string): LocatedPane | null {
  const win = windowOfInstance(frame, instanceId);
  return win ? locate(win, win.area.tabs.find((t) => t.instanceId === instanceId)!) : null;
}

export function listPanes(frame: FrameState): LocatedPane[] {
  return frame.windows.flatMap((win) => win.area.tabs.map((tab) => locate(win, tab)));
}

/**
 * The panes actually on screen: each non-minimized window's active tab. Distinct
 * from `listPanes`, which includes the background tabs — those are unmounted, so
 * their live state isn't readable anyway.
 *
 * A **minimized** window is excluded even though its pane stays mounted: "visible"
 * here means "the user can see it", which is what the agent's context builder and
 * the screenshot paths ask this for.
 */
export function visiblePanes(frame: FrameState): LocatedPane[] {
  return frame.windows.flatMap((win) => {
    if (win.mode === 'minimized') return [];
    const tab = win.area.tabs[win.area.activeTab];
    return tab ? [locate(win, tab)] : [];
  });
}

/** Apply `fn` to a pane instance wherever it lives. Null if unknown. */
export function updatePaneAnywhere(
  frame: FrameState,
  instanceId: string,
  fn: (pane: PaneState) => PaneState,
): FrameState | null {
  const win = windowOfInstance(frame, instanceId);
  if (!win) return null;
  return {
    ...frame,
    windows: frame.windows.map((w) =>
      w === win
        ? {
            ...w,
            area: {
              ...w.area,
              tabs: w.area.tabs.map((t) => (t.instanceId === instanceId ? fn(t) : t)),
            },
          }
        : w,
    ),
  };
}

/** Remove a pane instance from its window. Emptying a window closes it. */
export function removePaneAnywhere(
  frame: FrameState,
  instanceId: string,
): { frame: FrameState; removed: PaneState } | null {
  const win = windowOfInstance(frame, instanceId);
  if (!win) return null;
  const removed = win.area.tabs.find((t) => t.instanceId === instanceId)!;
  const tabs = win.area.tabs.filter((t) => t.instanceId !== instanceId);
  const windows = tabs.length
    ? frame.windows.map((w) =>
        w === win
          ? {
              ...w,
              area: { ...w.area, tabs, activeTab: Math.min(w.area.activeTab, tabs.length - 1) },
            }
          : w,
      )
    : // A window is its area: emptying the last tab closes the window rather than
      // leaving a titlebar with nothing under it.
      frame.windows.filter((w) => w !== win);
  return {
    frame: {
      ...frame,
      windows,
      focusedWindowId:
        tabs.length === 0 && frame.focusedWindowId === win.id ? null : frame.focusedWindowId,
    },
    removed,
  };
}
