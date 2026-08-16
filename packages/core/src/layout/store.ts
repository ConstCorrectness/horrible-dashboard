/**
 * THE layout store — one observable atom holding the active workspace id and
 * its frame, mutated only through the reducer below. UI renders from it via
 * `useSyncExternalStore`, the controller dispatches into it, and persistence
 * subscribes to autosave it. A no-op action returns the same snapshot reference
 * so subscribers don't re-render. Deterministic id allocation (see model.ts)
 * means a dispatcher holding the pre-dispatch snapshot can precompute the ids
 * an action will create (`areaId(frame.paneSeq)`, `instanceId(view, seq)`).
 */
import type { LayoutAction } from './actions';
import {
  areaId,
  areaOfInstance,
  createEmptyFrame,
  findArea,
  findAreaAnywhere,
  findPaneAnywhere,
  insertPane,
  joinArea,
  removePane,
  removePaneAnywhere,
  reorderTab,
  resizeArea,
  setActiveTab,
  setSplitSizes,
  splitArea,
  updateAreaAnywhere,
  updatePaneAnywhere,
  windowId,
} from './model';
import { cascadeRect, clampRect, rectForZone, rescaleRect } from './snap';
import { explodeToWindows, NOMINAL_VIEWPORT, tileWindows } from './windows';
import type {
  AreaNode,
  DockSide,
  FrameState,
  LayoutNode,
  LayoutStoreState,
  SnapZone,
  WindowRect,
  WindowState,
} from './types';

/**
 * Apply a center-tree editor to whichever host owns `areaId` — the center tree, or a
 * window's area.
 *
 * The tree editors in model.ts take *any* `LayoutNode` as their root and signal "no
 * such area" by returning null, and a window's bare `AreaNode` is a valid root. So the
 * identical function that retabs a center area retabs a window, and window tabs need
 * no second implementation to fall out of step with the first.
 */
function editArea(
  frame: FrameState,
  areaId: string,
  edit: (root: LayoutNode) => LayoutNode | null,
): FrameState | null {
  const center = edit(frame.center);
  if (center) return { ...frame, center };
  const win = frame.windows.find((w) => w.area.id === areaId);
  if (!win) return null;
  const next = edit(win.area);
  if (!next || next.kind !== 'area') return null;
  return {
    ...frame,
    windows: frame.windows.map((w) => (w === win ? { ...w, area: next } : w)),
  };
}

/** The next z, with the stack renormalized to 1..n so it can't grow without bound. */
function raiseToFront(windows: WindowState[], id: string): WindowState[] {
  const ordered = [...windows].sort((a, b) => a.z - b.z);
  const rest = ordered.filter((w) => w.id !== id);
  const target = ordered.find((w) => w.id === id);
  if (!target) return windows;
  const z = new Map<string, number>();
  rest.forEach((w, i) => z.set(w.id, i + 1));
  z.set(id, rest.length + 1);
  return windows.map((w) => (w.z === z.get(w.id) ? w : { ...w, z: z.get(w.id)! }));
}

function reduceFrame(frame: FrameState, action: LayoutAction): FrameState {
  switch (action.type) {
    case 'LOAD_WORKSPACE':
      return action.frame;

    case 'INSERT_PANE': {
      const next = editArea(frame, action.areaId, (root) =>
        insertPane(root, action.areaId, action.pane, { activate: action.activate }),
      );
      if (!next) return frame;
      return {
        ...next,
        paneSeq: frame.paneSeq + 1,
        // Only a center area can be the focused area; inserting into a window's
        // tab strip must not repoint the area verbs at something that isn't in
        // the center tree.
        focusedAreaId: findArea(next.center, action.areaId) ? action.areaId : frame.focusedAreaId,
      };
    }

    case 'REMOVE_PANE': {
      const res = removePaneAnywhere(frame, action.instanceId);
      return res ? res.frame : frame;
    }

    case 'MOVE_PANE': {
      const source = areaOfInstance(frame.center, action.instanceId);
      const target = findArea(frame.center, action.targetAreaId);
      if (!source || !target || source.id === target.id) return frame;
      const removed = removePane(frame.center, action.instanceId, frame.paneSeq);
      if (!removed) return frame;
      const center = insertPane(removed.root, action.targetAreaId, removed.removed);
      if (!center) return frame;
      return {
        ...frame,
        center,
        paneSeq: removed.seq,
        focusedAreaId: action.targetAreaId,
        fullscreenAreaId: findArea(center, frame.fullscreenAreaId ?? '')
          ? frame.fullscreenAreaId
          : null,
      };
    }

    case 'SET_ACTIVE_TAB': {
      const next = editArea(frame, action.areaId, (root) =>
        setActiveTab(root, action.areaId, action.index),
      );
      if (!next) return frame;
      return {
        ...next,
        focusedAreaId: findArea(next.center, action.areaId) ? action.areaId : frame.focusedAreaId,
      };
    }

    case 'REORDER_TAB': {
      const next = editArea(frame, action.areaId, (root) =>
        reorderTab(root, action.areaId, action.from, action.to),
      );
      return next ?? frame;
    }

    case 'SET_PANE_VIEW': {
      const next = updatePaneAnywhere(frame, action.instanceId, (pane) => ({
        ...pane,
        viewId: action.viewId,
        params: action.params,
        regions: action.regions,
      }));
      return next ?? frame;
    }

    case 'RETARGET_PANE': {
      if (action.instanceId === action.newInstanceId) return frame;
      // Refuse to mint a duplicate id — the caller falls back to a normal open,
      // which focuses whatever already holds the target identity.
      if (findPaneAnywhere(frame, action.newInstanceId)) return frame;
      const next = updatePaneAnywhere(frame, action.instanceId, (pane) => ({
        ...pane,
        instanceId: action.newInstanceId,
        params: action.params,
      }));
      if (!next) return frame;
      // Docks track their active tool by instance id, so follow the rename.
      const docks = { ...next.docks };
      for (const side of ['left', 'right', 'bottom'] as DockSide[]) {
        if (docks[side].activeTool === action.instanceId) {
          docks[side] = { ...docks[side], activeTool: action.newInstanceId };
        }
      }
      return { ...next, docks };
    }

    case 'SET_REGION': {
      const next = updatePaneAnywhere(frame, action.instanceId, (pane) => {
        const regions = { ...pane.regions };
        if (action.region) regions[action.position] = action.region;
        else delete regions[action.position];
        return { ...pane, regions: Object.keys(regions).length ? regions : undefined };
      });
      return next ?? frame;
    }

    case 'SET_SECTION': {
      const next = updatePaneAnywhere(frame, action.instanceId, (pane) =>
        pane.activeSection === action.section ? pane : { ...pane, activeSection: action.section },
      );
      return next ?? frame;
    }

    case 'SPLIT_AREA': {
      const res = splitArea(frame.center, action.areaId, action.direction, frame.paneSeq);
      if (!res) return frame;
      let center = res.root;
      let seq = res.seq;
      if (action.pane) {
        const withPane = insertPane(center, res.newAreaId, action.pane);
        if (withPane) {
          center = withPane;
          seq += 1;
        }
      }
      return { ...frame, center, paneSeq: seq, focusedAreaId: res.newAreaId };
    }

    case 'JOIN_AREA': {
      const res = joinArea(frame.center, action.areaId, action.direction);
      if (!res) return frame;
      let center = res.root;
      if (action.adoptTabs && res.removed.tabs.length > 0) {
        const host = findArea(center, action.areaId);
        if (host) {
          for (const tab of res.removed.tabs) {
            const next = insertPane(center, action.areaId, tab, { activate: false });
            if (next) center = next;
          }
        }
      }
      const fullscreenGone = frame.fullscreenAreaId === res.removed.id;
      return {
        ...frame,
        center,
        focusedAreaId: action.areaId,
        fullscreenAreaId: fullscreenGone ? null : frame.fullscreenAreaId,
      };
    }

    case 'SET_SPLIT_SIZES': {
      const center = setSplitSizes(frame.center, action.splitId, action.sizes);
      return center ? { ...frame, center } : frame;
    }

    case 'RESIZE_AREA': {
      const center = resizeArea(frame.center, action.areaId, action.target);
      return center ? { ...frame, center } : frame;
    }

    case 'FOCUS_AREA': {
      if (frame.focusedAreaId === action.areaId || !findArea(frame.center, action.areaId)) {
        return frame;
      }
      return { ...frame, focusedAreaId: action.areaId };
    }

    case 'FOCUS_PANE': {
      if (action.instanceId === null) {
        return frame.focusedInstanceId === null ? frame : { ...frame, focusedInstanceId: null };
      }
      const located = findPaneAnywhere(frame, action.instanceId);
      if (!located) return frame;
      // A center pane drags the focused area along with it; a docked or floating
      // pane leaves it alone, because area verbs still need a center target.
      const focusedAreaId =
        located.location.kind === 'area' ? located.location.areaId : frame.focusedAreaId;
      if (frame.focusedInstanceId === action.instanceId && frame.focusedAreaId === focusedAreaId) {
        return frame;
      }
      return { ...frame, focusedInstanceId: action.instanceId, focusedAreaId };
    }

    case 'SET_FULLSCREEN': {
      const valid = action.areaId === null || findArea(frame.center, action.areaId) !== null;
      if (!valid || frame.fullscreenAreaId === action.areaId) return frame;
      return { ...frame, fullscreenAreaId: action.areaId };
    }

    case 'SET_HEADER_COLLAPSED': {
      const found = findAreaAnywhere(frame, action.areaId);
      if (!found || (found.area.headerCollapsed ?? false) === action.collapsed) return frame;
      const next = updateAreaAnywhere(frame, action.areaId, (area) => ({
        ...area,
        headerCollapsed: action.collapsed,
      }));
      return next ?? frame;
    }

    case 'SET_DOCK': {
      const dock = frame.docks[action.side];
      const next = { ...dock, ...action.patch };
      // A dock with no tools has nothing to show — it can resize but not open.
      if (next.visible && next.tools.length === 0) next.visible = false;
      if (next.visible === dock.visible && next.size === dock.size) return frame;
      return { ...frame, docks: { ...frame.docks, [action.side]: next } };
    }

    case 'INSERT_TOOL': {
      const dock = frame.docks[action.side];
      const tools = [...dock.tools, action.pane];
      return {
        ...frame,
        paneSeq: frame.paneSeq + 1,
        docks: {
          ...frame.docks,
          [action.side]: {
            ...dock,
            tools,
            visible: action.activate === false ? dock.visible : true,
            activeTool:
              action.activate === false
                ? (dock.activeTool ?? action.pane.instanceId)
                : action.pane.instanceId,
          },
        },
      };
    }

    case 'SET_ACTIVE_TOOL': {
      const dock = frame.docks[action.side];
      if (!dock.tools.some((t) => t.instanceId === action.instanceId)) return frame;
      return {
        ...frame,
        docks: {
          ...frame.docks,
          [action.side]: { ...dock, activeTool: action.instanceId, visible: true },
        },
      };
    }

    case 'MOVE_TOOL': {
      const located = findPaneAnywhere(frame, action.instanceId);
      if (!located) return frame;
      if (located.location.kind === 'dock' && located.location.dock === action.side) return frame;
      const wasShowing =
        (located.location.kind === 'dock' &&
          frame.docks[located.location.dock].visible &&
          frame.docks[located.location.dock].activeTool === action.instanceId) ||
        located.location.kind !== 'dock';
      const res = removePaneAnywhere(frame, action.instanceId);
      if (!res) return frame;
      const dock = res.frame.docks[action.side];
      return {
        ...res.frame,
        docks: {
          ...res.frame.docks,
          [action.side]: {
            ...dock,
            tools: [...dock.tools, res.removed],
            activeTool: res.removed.instanceId,
            visible: dock.visible || wasShowing,
          },
        },
      };
    }

    case 'UNDOCK_PANE_TO_AREA': {
      const target = findArea(frame.center, action.areaId);
      if (!target) return frame;
      const located = findPaneAnywhere(frame, action.instanceId);
      // Already sitting in the destination — nothing to do.
      if (
        !located ||
        (located.location.kind === 'area' && located.location.areaId === action.areaId)
      )
        return frame;
      const res = removePaneAnywhere(frame, action.instanceId);
      if (!res) return frame;
      // Re-find the area: removing the pane can collapse a split and rebuild the
      // tree, so the pre-removal node is stale.
      if (!findArea(res.frame.center, action.areaId)) return frame;
      const center = insertPane(res.frame.center, action.areaId, res.removed);
      if (!center) return frame;
      return { ...res.frame, center, focusedAreaId: action.areaId };
    }

    case 'SET_TOOL_SIZE': {
      const dock = frame.docks[action.side];
      const tool = dock.tools.find((t) => t.instanceId === action.instanceId);
      if (!tool || tool.dockSize === action.size) return frame;
      return {
        ...frame,
        docks: {
          ...frame.docks,
          [action.side]: {
            ...dock,
            // Mirrored into the dock so the NEXT tool opened here inherits this
            // width instead of snapping back to the built-in default.
            size: action.size,
            tools: dock.tools.map((t) =>
              t.instanceId === action.instanceId ? { ...t, dockSize: action.size } : t,
            ),
          },
        },
      };
    }

    case 'WINDOW_FROM_PANE': {
      const located = findPaneAnywhere(frame, action.instanceId);
      // Already alone in its own window — popping it out again is a no-op rather
      // than a second window holding the same pane.
      if (!located) return frame;
      const from = located.location;
      if (from.kind === 'window') {
        const win = frame.windows.find((w) => w.id === from.windowId);
        if (win && win.area.tabs.length === 1) return frame;
      }
      const res = removePaneAnywhere(frame, action.instanceId);
      if (!res) return frame;
      const viewport = frame.windowViewport ?? NOMINAL_VIEWPORT;
      const id = windowId(res.frame.paneSeq);
      const area: AreaNode = {
        kind: 'area',
        id: areaId(res.frame.paneSeq + 1),
        tabs: [res.removed],
        activeTab: 0,
      };
      const rect = clampRect(
        action.rect ?? cascadeRect(res.frame.windows.length, viewport),
        viewport,
      );
      return {
        ...res.frame,
        // Two ids minted (the window and its area), so the counter moves by two.
        paneSeq: res.frame.paneSeq + 2,
        windows: [
          ...res.frame.windows,
          { id, area, rect, mode: 'normal', z: res.frame.windows.length + 1 },
        ],
        focusedWindowId: id,
      };
    }

    case 'DOCK_WINDOW': {
      const win = frame.windows.find((w) => w.id === action.windowId);
      if (!win) return frame;
      const targetId = action.areaId ?? frame.focusedAreaId;
      if (!targetId || !findArea(frame.center, targetId)) return frame;
      let center: LayoutNode | null = frame.center;
      // Every tab comes back, not just the active one: a window is its area, and
      // silently dropping its background tabs would lose the user's panes.
      for (const tab of win.area.tabs) {
        const next: LayoutNode | null = insertPane(center, targetId, tab, { activate: false });
        if (next) center = next;
      }
      if (center === frame.center) return frame;
      return {
        ...frame,
        center,
        windows: frame.windows.filter((w) => w !== win),
        focusedAreaId: targetId,
        focusedWindowId: frame.focusedWindowId === win.id ? null : frame.focusedWindowId,
      };
    }

    case 'SET_WINDOW_RECT': {
      const win = frame.windows.find((w) => w.id === action.windowId);
      if (!win) return frame;
      // Clamping lives here rather than in the pointer handler so that a rect
      // arriving from an agent tool obeys exactly the same rules as one dragged.
      const rect = clampRect(action.rect, frame.windowViewport ?? NOMINAL_VIEWPORT);
      if (sameRect(win.rect, rect) && !win.snap && win.mode === 'normal') return frame;
      return {
        ...frame,
        windows: frame.windows.map((w) =>
          w === win
            ? // Moving or resizing by hand un-snaps and un-maximizes: the window is
              // now wherever the user put it, and springing back later would be a
              // ghost the user cannot explain.
              { ...w, rect, mode: w.mode === 'minimized' ? w.mode : 'normal', snap: undefined }
            : w,
        ),
      };
    }

    case 'BRING_WINDOW_FRONT': {
      const win = frame.windows.find((w) => w.id === action.windowId);
      if (!win) return frame;
      const windows = raiseToFront(frame.windows, action.windowId);
      if (windows.every((w, i) => w === frame.windows[i])) return frame;
      return { ...frame, windows };
    }

    case 'FOCUS_WINDOW': {
      if (action.windowId !== null && !frame.windows.some((w) => w.id === action.windowId)) {
        return frame;
      }
      if (frame.focusedWindowId === action.windowId) return frame;
      return { ...frame, focusedWindowId: action.windowId };
    }

    case 'SET_WINDOW_MODE': {
      const win = frame.windows.find((w) => w.id === action.windowId);
      if (!win) return frame;
      const viewport = action.viewport ?? frame.windowViewport ?? NOMINAL_VIEWPORT;
      const next = ((): WindowState => {
        if (action.mode === 'minimized') {
          // Geometry untouched: restoring must return exactly where it was.
          return { ...win, mode: 'minimized' };
        }
        if (action.mode === 'maximized' || action.snap) {
          const zone: SnapZone = action.snap ?? 'max';
          return {
            ...win,
            mode: action.mode === 'maximized' ? 'maximized' : 'normal',
            snap: action.snap,
            // Only capture a restore rect when leaving a free-floating state —
            // maximizing an already-snapped window must not overwrite the rect it
            // was originally dragged to.
            restoreRect: win.snap || win.mode === 'maximized' ? win.restoreRect : win.rect,
            rect: rectForZone(zone, viewport),
          };
        }
        // 'normal': come back to the remembered rect, if there is one.
        return {
          ...win,
          mode: 'normal',
          snap: undefined,
          rect: win.restoreRect ? clampRect(win.restoreRect, viewport) : win.rect,
          restoreRect: undefined,
        };
      })();
      return {
        ...frame,
        windows: raiseToFront(
          frame.windows.map((w) => (w === win ? next : w)),
          // A window being un-minimized comes to the front; one being minimized
          // must not, or it would raise itself on the way out.
          action.mode === 'minimized' ? '' : win.id,
        ),
      };
    }

    case 'MERGE_INTO_WINDOW': {
      const target = frame.windows.find((w) => w.id === action.windowId);
      if (!target) return frame;
      const located = findPaneAnywhere(frame, action.instanceId);
      if (!located) return frame;
      if (located.location.kind === 'window' && located.location.windowId === target.id) {
        return frame;
      }
      const res = removePaneAnywhere(frame, action.instanceId);
      if (!res) return frame;
      // Re-find: removing the pane may have closed the source window, and could in
      // principle have been the target itself.
      const host = res.frame.windows.find((w) => w.id === action.windowId);
      if (!host) return frame;
      const merged = insertPane(host.area, host.area.id, res.removed);
      if (!merged || merged.kind !== 'area') return frame;
      const reordered =
        action.index === undefined
          ? merged
          : ((reorderTab(merged, merged.id, merged.tabs.length - 1, action.index) ??
              merged) as AreaNode);
      return {
        ...res.frame,
        windows: res.frame.windows.map((w) => (w.id === host.id ? { ...w, area: reordered } : w)),
        focusedWindowId: host.id,
      };
    }

    case 'SET_WINDOW_VIEWPORT': {
      const { viewport } = action;
      if (viewport.w <= 0 || viewport.h <= 0) return frame;
      const from = frame.windowViewport;
      if (from && from.w === viewport.w && from.h === viewport.h) return frame;
      if (!from) return { ...frame, windowViewport: viewport };
      return {
        ...frame,
        windowViewport: viewport,
        windows: frame.windows.map((w) => {
          // A snapped or maximized window re-derives its rect from the zone rather
          // than being scaled: half of the new surface is exactly half, where a
          // scaled rect would be off by the rounding of the old one.
          const zone: SnapZone | null = w.snap ?? (w.mode === 'maximized' ? 'max' : null);
          const rect = zone
            ? rectForZone(zone, viewport)
            : clampRect(rescaleRect(w.rect, from, viewport), viewport);
          return {
            ...w,
            rect,
            restoreRect: w.restoreRect ? rescaleRect(w.restoreRect, from, viewport) : undefined,
          };
        }),
      };
    }

    case 'SET_DESKTOP_MODE': {
      if (frame.mode === action.mode) return frame;
      const next =
        action.mode === 'floating'
          ? explodeToWindows(frame, action.viewport)
          : tileWindows(frame, action.dockFor ?? {});
      return { ...next, mode: action.mode };
    }

    case 'SET_BACKDROP': {
      const { backdrop } = action;
      if (
        frame.backdrop.id === backdrop.id &&
        JSON.stringify(frame.backdrop.params ?? null) === JSON.stringify(backdrop.params ?? null)
      ) {
        return frame;
      }
      return { ...frame, backdrop };
    }
  }
}

function sameRect(a: WindowRect, b: WindowRect): boolean {
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
}

/**
 * Drop `focusedInstanceId`/`focusedWindowId` when they no longer name live things.
 * Done here, once, after every action rather than inside each closing verb —
 * REMOVE_PANE, JOIN_AREA, RETARGET_PANE, DOCK_WINDOW and the mode switch can all
 * orphan one, and a missed case would leave pane-scoped keybindings resolving
 * against a pane that isn't on screen.
 */
function sanitizeFocus(frame: FrameState): FrameState {
  let next = frame;
  if (next.focusedInstanceId && !findPaneAnywhere(next, next.focusedInstanceId)) {
    next = { ...next, focusedInstanceId: null };
  }
  if (next.focusedWindowId && !next.windows.some((w) => w.id === next.focusedWindowId)) {
    next = { ...next, focusedWindowId: null };
  }
  return next;
}

function reduce(state: LayoutStoreState, action: LayoutAction): LayoutStoreState {
  if (action.type === 'LOAD_WORKSPACE') {
    return {
      workspaceId: action.workspaceId,
      frame: sanitizeFocus(action.frame),
      hydrated: true,
      revision: state.revision, // loads are not user edits — autosave stays clean
    };
  }
  const frame = sanitizeFocus(reduceFrame(state.frame, action));
  if (frame === state.frame) return state;
  // A viewport change is a *projection* of the same layout onto a differently
  // sized surface, not a user edit. Bumping `revision` for it would make every
  // browser resize dirty the workspace, and the 600ms autosave debounce would
  // turn a slow window-drag of the app edge into a continuous stream of PUTs.
  if (action.type === 'SET_WINDOW_VIEWPORT') return { ...state, frame };
  return { ...state, frame, revision: state.revision + 1 };
}

const initialState: LayoutStoreState = {
  workspaceId: null,
  frame: createEmptyFrame(),
  hydrated: false,
  revision: 0,
};

let state = initialState;
const listeners = new Set<() => void>();

export const layoutStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  /** Stable reference between dispatches — safe for useSyncExternalStore. */
  getSnapshot(): LayoutStoreState {
    return state;
  },
  /** Apply an action; returns the (possibly unchanged) new snapshot. */
  dispatch(action: LayoutAction): LayoutStoreState {
    const next = reduce(state, action);
    if (next !== state) {
      state = next;
      for (const listener of listeners) listener();
    }
    return state;
  },
  /** Test-only: reset to the pristine boot state. */
  resetForTests(): void {
    state = initialState;
    for (const listener of listeners) listener();
  },
};
