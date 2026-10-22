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
  createEmptyFrame,
  findAreaAnywhere,
  findPaneAnywhere,
  insertPane,
  removePaneAnywhere,
  reorderTab,
  setActiveTab,
  updateAreaAnywhere,
  updatePaneAnywhere,
  windowId,
} from './model';
import {
  cascadeRect,
  clampRect,
  complementZone,
  fillTarget,
  NOMINAL_VIEWPORT,
  rectForZone,
  rescaleRect,
} from './snap';
import type {
  AreaNode,
  FrameState,
  LayoutStoreState,
  PaneState,
  SnapZone,
  WindowRect,
  WindowState,
} from './types';

/**
 * Apply a tab editor to the window that owns `areaId`. The editor returns null for
 * "no change", which becomes null here too.
 */
function editArea(
  frame: FrameState,
  areaId: string,
  edit: (area: AreaNode) => AreaNode | null,
): FrameState | null {
  const found = findAreaAnywhere(frame, areaId);
  const next = found && edit(found.area);
  if (!next) return null;
  return updateAreaAnywhere(frame, areaId, () => next);
}

/** A fresh window holding `pane`, at ids `w<seq>`/`a<seq+1>`, stacked on top. */
function newWindow(frame: FrameState, pane: PaneState, seq: number, rect?: WindowRect): FrameState {
  const viewport = frame.windowViewport ?? NOMINAL_VIEWPORT;
  const id = windowId(seq);
  const win: WindowState = {
    id,
    area: { kind: 'area', id: areaId(seq + 1), tabs: [pane], activeTab: 0 },
    rect: clampRect(rect ?? cascadeRect(frame.windows.length, viewport), viewport),
    mode: 'normal',
    z: frame.windows.reduce((max, w) => Math.max(max, w.z), 0) + 1,
  };
  return { ...frame, windows: [...frame.windows, win], focusedWindowId: id };
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
      const next = editArea(frame, action.areaId, (area) =>
        insertPane(area, action.pane, { activate: action.activate }),
      );
      return next ? { ...next, paneSeq: frame.paneSeq + 1 } : frame;
    }

    case 'REMOVE_PANE': {
      const res = removePaneAnywhere(frame, action.instanceId);
      return res ? res.frame : frame;
    }

    case 'SET_ACTIVE_TAB': {
      const next = editArea(frame, action.areaId, (area) => setActiveTab(area, action.index));
      return next ?? frame;
    }

    case 'REORDER_TAB': {
      const next = editArea(frame, action.areaId, (area) =>
        reorderTab(area, action.from, action.to),
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
      return next ?? frame;
    }

    case 'SET_PANE_PARAMS': {
      const next = updatePaneAnywhere(frame, action.instanceId, (pane) => ({
        ...pane,
        params: action.params,
      }));
      return next ?? frame;
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

    case 'FOCUS_PANE': {
      if (action.instanceId === null) {
        return frame.focusedInstanceId === null ? frame : { ...frame, focusedInstanceId: null };
      }
      if (frame.focusedInstanceId === action.instanceId) return frame;
      if (!findPaneAnywhere(frame, action.instanceId)) return frame;
      return { ...frame, focusedInstanceId: action.instanceId };
    }

    case 'SET_PRESENTED': {
      if (frame.presentedInstanceId === action.instanceId) return frame;
      if (action.instanceId !== null && !findPaneAnywhere(frame, action.instanceId)) return frame;
      return { ...frame, presentedInstanceId: action.instanceId };
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

    case 'SET_PANE_ATTENTION': {
      const located = findPaneAnywhere(frame, action.instanceId);
      if (!located) return frame;
      if ((located.pane.attention ?? false) === action.attention) return frame;
      return (
        updatePaneAnywhere(frame, action.instanceId, (pane) => {
          const next = { ...pane };
          // Absent rather than `false`, so a serialized layout does not carry a
          // stale "was flashing once" flag for every pane the user ever opened.
          if (action.attention) next.attention = true;
          else delete next.attention;
          return next;
        }) ?? frame
      );
    }
    case 'OPEN_WINDOW': {
      if (findPaneAnywhere(frame, action.pane.instanceId)) return frame;
      const seq = frame.paneSeq;
      // The pane's id may have been minted from `seq`; the window and its area
      // take the two after it.
      return { ...newWindow(frame, action.pane, seq + 1, action.rect), paneSeq: seq + 3 };
    }

    case 'WINDOW_FROM_PANE': {
      const located = findPaneAnywhere(frame, action.instanceId);
      if (!located) return frame;
      // Already alone in its own window — tearing it out again is a no-op rather
      // than a second window holding the same pane.
      const from = frame.windows.find((w) => w.id === located.location.windowId);
      if (from && from.area.tabs.length === 1) return frame;
      const res = removePaneAnywhere(frame, action.instanceId);
      if (!res) return frame;
      const seq = res.frame.paneSeq;
      // Two ids minted (the window and its area), so the counter moves by two.
      return { ...newWindow(res.frame, res.removed, seq, action.rect), paneSeq: seq + 2 };
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
              {
                ...w,
                rect,
                mode: w.mode === 'minimized' ? w.mode : 'normal',
                snap: undefined,
                // A rect placed by hand is where this window belongs now, so a
                // minimized one must not resume its old zone on restore — and the
                // restore rect goes with it, or un-maximizing later springs the
                // window back to a size the user replaced by dragging.
                minimizedFrom: undefined,
                restoreRect: undefined,
              }
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
          //
          // The *mode* is not untouched, though — `WindowMode` is one enum, so
          // 'minimized' overwrites 'maximized'. Record what it was, or the restore
          // below takes the free-floating path and springs a maximized window back
          // to its pre-maximize size. Re-minimizing must not overwrite the record
          // with 'normal'.
          if (win.mode === 'minimized') return win;
          return {
            ...win,
            mode: 'minimized',
            minimizedFrom: {
              mode: win.mode === 'maximized' ? 'maximized' : 'normal',
              ...(win.snap ? { snap: win.snap } : {}),
            },
          };
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
            minimizedFrom: undefined,
            rect: rectForZone(zone, viewport),
          };
        }
        // Un-minimizing: go back to whatever it was, maximized or snapped
        // included. `restoreRect` is left alone — it belongs to the maximize that
        // is being resumed, not consumed by this restore.
        if (win.mode === 'minimized' && win.minimizedFrom) {
          const { mode, snap } = win.minimizedFrom;
          const zone: SnapZone | null = snap ?? (mode === 'maximized' ? 'max' : null);
          return {
            ...win,
            mode,
            snap,
            minimizedFrom: undefined,
            // Re-derived rather than replayed: the surface may have resized while
            // the window was minimized, and half of the new surface is exactly
            // half where a stale rect would be off by the old one's rounding.
            rect: zone ? rectForZone(zone, viewport) : clampRect(win.rect, viewport),
          };
        }
        // 'normal': come back to the remembered rect, if there is one.
        return {
          ...win,
          mode: 'normal',
          snap: undefined,
          minimizedFrom: undefined,
          rect: win.restoreRect ? clampRect(win.restoreRect, viewport) : win.rect,
          restoreRect: undefined,
        };
      })();
      // Snap-assist fill: the window just took a half, so whatever was covering the
      // other half moves into it and the user gets the split they were aiming at.
      //
      // One window only, the topmost candidate — filling every overlapping window
      // into the same zone would stack them, and the second one to move would be a
      // window the user never touched appearing from behind the first.
      const filled = ((): WindowState | null => {
        if (!action.fill || next.mode === 'minimized') return null;
        const zone = next.snap ?? (next.mode === 'maximized' ? 'max' : null);
        const other = zone ? complementZone(zone) : null;
        if (!zone || !other) return null;
        const target = fillTarget(frame.windows, win.id, zone, viewport);
        if (!target) return null;
        return {
          ...target,
          mode: 'normal',
          snap: other,
          minimizedFrom: undefined,
          // Same rule as the branch above: only capture a restore rect when leaving
          // a free-floating state, so filling an already-maximized window keeps the
          // rect it had before it was maximized.
          restoreRect:
            target.snap || target.mode === 'maximized' ? target.restoreRect : target.rect,
          rect: rectForZone(other, viewport),
        };
      })();
      return {
        ...frame,
        windows: raiseToFront(
          frame.windows.map((w) =>
            w === win ? next : filled && w.id === filled.id ? filled : w,
          ),
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
      const merged = insertPane(host.area, res.removed);
      const reordered =
        action.index === undefined
          ? merged
          : (reorderTab(merged, merged.tabs.length - 1, action.index) ?? merged);
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
          // A minimized window's zone lives in `minimizedFrom`: its own `mode` is
          // 'minimized', so reading only `mode` would rescale a maximized-then-
          // minimized window and it would come back smaller than the surface.
          const zone: SnapZone | null =
            w.mode === 'minimized' && w.minimizedFrom
              ? (w.minimizedFrom.snap ?? (w.minimizedFrom.mode === 'maximized' ? 'max' : null))
              : (w.snap ?? (w.mode === 'maximized' ? 'max' : null));
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
 * REMOVE_PANE, RETARGET_PANE, WINDOW_FROM_PANE and MERGE_INTO_WINDOW can all
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
  // Same argument for the presented pane, and it matters more: presentation
  // hides the taskbar, so a stale id is not a mis-aimed keybinding but a screen
  // with nothing on it and no chrome to escape by.
  if (next.presentedInstanceId && !findPaneAnywhere(next, next.presentedInstanceId)) {
    next = { ...next, presentedInstanceId: null };
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
