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
  areaOfInstance,
  createEmptyFrame,
  findArea,
  findPaneAnywhere,
  insertPane,
  joinArea,
  removePane,
  removePaneAnywhere,
  resizeArea,
  setActiveTab,
  setSplitSizes,
  splitArea,
  updatePaneAnywhere,
} from './model';
import type { AreaNode, DockSide, FrameState, LayoutStoreState } from './types';

function reduceFrame(frame: FrameState, action: LayoutAction): FrameState {
  switch (action.type) {
    case 'LOAD_WORKSPACE':
      return action.frame;

    case 'INSERT_PANE': {
      const center = insertPane(frame.center, action.areaId, action.pane, {
        activate: action.activate,
      });
      if (!center) return frame;
      return { ...frame, center, paneSeq: frame.paneSeq + 1, focusedAreaId: action.areaId };
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
      const center = setActiveTab(frame.center, action.areaId, action.index);
      return center ? { ...frame, center, focusedAreaId: action.areaId } : frame;
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

    case 'SET_FULLSCREEN': {
      const valid = action.areaId === null || findArea(frame.center, action.areaId) !== null;
      if (!valid || frame.fullscreenAreaId === action.areaId) return frame;
      return { ...frame, fullscreenAreaId: action.areaId };
    }

    case 'SET_HEADER_COLLAPSED': {
      let found = false;
      const center = ((): FrameState['center'] | null => {
        const area = findArea(frame.center, action.areaId);
        if (!area || (area.headerCollapsed ?? false) === action.collapsed) return null;
        found = true;
        return replaceArea(frame.center, area, { ...area, headerCollapsed: action.collapsed });
      })();
      return found && center ? { ...frame, center } : frame;
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

    case 'FLOAT_PANE': {
      const located = findPaneAnywhere(frame, action.instanceId);
      if (!located || located.location.kind === 'floating') return frame;
      const res = removePaneAnywhere(frame, action.instanceId);
      if (!res) return frame;
      const z = Math.max(0, ...res.frame.floating.map((f) => f.z)) + 1;
      return {
        ...res.frame,
        floating: [
          ...res.frame.floating,
          { pane: res.removed, rect: action.rect ?? { x: 0.2, y: 0.15, w: 0.5, h: 0.55 }, z },
        ],
      };
    }

    case 'DOCK_FLOATING': {
      const float = frame.floating.find((f) => f.pane.instanceId === action.instanceId);
      if (!float) return frame;
      const targetId = action.areaId ?? frame.focusedAreaId;
      if (!targetId || !findArea(frame.center, targetId)) return frame;
      const center = insertPane(frame.center, targetId, float.pane);
      if (!center) return frame;
      return {
        ...frame,
        center,
        floating: frame.floating.filter((f) => f !== float),
        focusedAreaId: targetId,
      };
    }

    case 'SET_FLOATING_RECT': {
      const floating = frame.floating.map((f) =>
        f.pane.instanceId === action.instanceId ? { ...f, rect: action.rect } : f,
      );
      return { ...frame, floating };
    }

    case 'BRING_FLOATING_FRONT': {
      const target = frame.floating.find((f) => f.pane.instanceId === action.instanceId);
      if (!target) return frame;
      const top = Math.max(...frame.floating.map((f) => f.z));
      if (target.z === top) return frame;
      return {
        ...frame,
        floating: frame.floating.map((f) => (f === target ? { ...f, z: top + 1 } : f)),
      };
    }
  }
}

/** Swap one known area node for another (identity-based, used for tiny patches). */
function replaceArea(
  root: FrameState['center'],
  from: AreaNode,
  to: AreaNode,
): FrameState['center'] {
  if (root === from) return to;
  if (root.kind === 'area') return root;
  return { ...root, children: root.children.map((c) => replaceArea(c, from, to)) };
}

function reduce(state: LayoutStoreState, action: LayoutAction): LayoutStoreState {
  if (action.type === 'LOAD_WORKSPACE') {
    return {
      workspaceId: action.workspaceId,
      frame: action.frame,
      hydrated: true,
      revision: state.revision, // loads are not user edits — autosave stays clean
    };
  }
  const frame = reduceFrame(state.frame, action);
  if (frame === state.frame) return state;
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
