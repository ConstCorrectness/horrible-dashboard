/**
 * The workbench reducer: mechanical edits to a `DockFrame`.
 *
 * Same contract as the v3 reducer in `store.ts`. Role routing, singleton rules
 * and "where does this view belong" are resolved by the controller before
 * dispatch, so this never needs the registry; a no-op returns the same frame
 * reference so subscribers do not re-render; and every result is `sanitize`d so
 * no action can leave focus, a maximize or a presentation naming something gone.
 *
 */
import { reorderTab, setActiveTab } from '../model';
import { arrangeCenter, type ArrangeStyle } from './arrange';
import type { BackdropRef, PaneState, RegionPosition, RegionState } from '../types';
import {
  dockPopout,
  findAreaAnywhere,
  findPane,
  focusArea,
  focusPane,
  insertPane,
  movePane,
  removePane,
  sanitize,
  setDockSize,
  setDockVisible,
  setPopoutBounds,
  setSplitSizes,
  toggleMaximized,
  updateAreaAnywhere,
  updatePane,
} from './frame';
import type { DockFrame, DockSide, PlaceTarget, PopoutBounds } from './types';

export type DockAction =
  // Panes
  /**
   * Open `pane` at `target`. The pane's id is assumed minted from `paneSeq` (the
   * controller's `instanceId(view, frame.paneSeq)`), so the counter steps past it
   * before any group id is drawn — the same rule the v3 `INSERT_PANE` followed.
   */
  | { type: 'INSERT_PANE'; pane: PaneState; target: PlaceTarget; activate?: boolean }
  | { type: 'REMOVE_PANE'; instanceId: string }
  | { type: 'MOVE_PANE'; instanceId: string; target: PlaceTarget }
  | { type: 'SET_ACTIVE_TAB'; areaId: string; index: number }
  | { type: 'REORDER_TAB'; areaId: string; from: number; to: number }
  | {
      type: 'SET_PANE_VIEW';
      instanceId: string;
      viewId: string;
      params?: Record<string, unknown>;
      regions?: PaneState['regions'];
    }
  | {
      type: 'RETARGET_PANE';
      instanceId: string;
      newInstanceId: string;
      params?: Record<string, unknown>;
    }
  | { type: 'SET_PANE_PARAMS'; instanceId: string; params?: Record<string, unknown> }
  | { type: 'SET_REGION'; instanceId: string; position: RegionPosition; region: RegionState | null }
  | { type: 'SET_SECTION'; instanceId: string; section: string }
  | { type: 'SET_PANE_ATTENTION'; instanceId: string; attention: boolean }
  // Focus and presentation
  | { type: 'FOCUS_PANE'; instanceId: string }
  | { type: 'FOCUS_AREA'; areaId: string }
  | { type: 'SET_PRESENTED'; instanceId: string | null }
  | { type: 'TOGGLE_MAXIMIZED'; areaId: string }
  // Structure
  | { type: 'SET_SPLIT_SIZES'; splitId: string; sizes: number[] }
  /** `visible` omitted toggles. */
  | { type: 'SET_DOCK_VISIBLE'; side: DockSide; visible?: boolean }
  | { type: 'SET_DOCK_SIZE'; side: DockSide; size: number }
  /** Rebuild the centre tree in one move; docks and popouts are untouched. */
  | { type: 'ARRANGE_CENTER'; style: ArrangeStyle }
  // Popouts
  | {
      type: 'SET_POPOUT_BOUNDS';
      popoutId: string;
      bounds?: PopoutBounds;
      displayId?: number;
      maximized?: boolean;
    }
  /**
   * The popout window was closed: put its panes back. `targets` is the
   * controller's routing for each pane by instance id; a pane it does not name
   * goes to the centre. Never closes a pane.
   */
  | { type: 'DOCK_POPOUT'; popoutId: string; targets?: Record<string, PlaceTarget> }
  // Desktop
  | { type: 'SET_BACKDROP'; backdrop: BackdropRef };

export function dockReduce(frame: DockFrame, action: DockAction): DockFrame {
  const next = reduceOnce(frame, action);
  return next === frame ? frame : sanitize(next);
}

function reduceOnce(frame: DockFrame, action: DockAction): DockFrame {
  switch (action.type) {
    case 'INSERT_PANE': {
      // An instance id is one place: opening it again is the controller's
      // "focus what is open", never a second copy.
      if (findPane(frame, action.pane.instanceId)) return frame;
      return (
        insertPane({ ...frame, paneSeq: frame.paneSeq + 1 }, action.pane, action.target, {
          activate: action.activate,
        }) ?? frame
      );
    }

    case 'REMOVE_PANE':
      return removePane(frame, action.instanceId)?.frame ?? frame;

    case 'MOVE_PANE':
      return movePane(frame, action.instanceId, action.target) ?? frame;

    case 'SET_ACTIVE_TAB': {
      const found = findAreaAnywhere(frame, action.areaId);
      if (!found || found.area.activeTab === action.index) return frame;
      const area = setActiveTab(found.area, action.index);
      return area ? (updateAreaAnywhere(frame, action.areaId, () => area) ?? frame) : frame;
    }

    case 'REORDER_TAB': {
      const found = findAreaAnywhere(frame, action.areaId);
      const area = found && reorderTab(found.area, action.from, action.to);
      return area ? (updateAreaAnywhere(frame, action.areaId, () => area) ?? frame) : frame;
    }

    case 'SET_PANE_VIEW':
      return (
        updatePane(frame, action.instanceId, (pane) => ({
          ...pane,
          viewId: action.viewId,
          params: action.params,
          regions: action.regions,
        })) ?? frame
      );

    case 'RETARGET_PANE': {
      if (action.instanceId === action.newInstanceId) return frame;
      // Refuse to mint a duplicate identity; the caller falls back to a normal
      // open, which focuses whatever already holds it.
      if (findPane(frame, action.newInstanceId)) return frame;
      const next = updatePane(frame, action.instanceId, (pane) => ({
        ...pane,
        instanceId: action.newInstanceId,
        params: action.params,
      }));
      if (!next) return frame;
      const follow = (id: string | null) => (id === action.instanceId ? action.newInstanceId : id);
      return {
        ...next,
        focusedInstanceId: follow(next.focusedInstanceId),
        presentedInstanceId: follow(next.presentedInstanceId),
      };
    }

    case 'SET_PANE_PARAMS':
      return (
        updatePane(frame, action.instanceId, (pane) => ({ ...pane, params: action.params })) ??
        frame
      );

    case 'SET_REGION':
      return (
        updatePane(frame, action.instanceId, (pane) => {
          const regions = { ...pane.regions };
          if (action.region) regions[action.position] = action.region;
          else delete regions[action.position];
          return { ...pane, regions: Object.keys(regions).length ? regions : undefined };
        }) ?? frame
      );

    case 'SET_SECTION': {
      const located = findPane(frame, action.instanceId);
      if (!located || located.pane.activeSection === action.section) return frame;
      return (
        updatePane(frame, action.instanceId, (pane) => ({
          ...pane,
          activeSection: action.section,
        })) ?? frame
      );
    }

    case 'SET_PANE_ATTENTION': {
      const located = findPane(frame, action.instanceId);
      if (!located || (located.pane.attention ?? false) === action.attention) return frame;
      return (
        updatePane(frame, action.instanceId, (pane) => {
          const next = { ...pane };
          // Absent rather than false, so a saved layout does not carry a stale
          // flag for every pane that ever finished a job.
          if (action.attention) next.attention = true;
          else delete next.attention;
          return next;
        }) ?? frame
      );
    }

    case 'FOCUS_PANE': {
      if (frame.focusedInstanceId === action.instanceId) {
        // Already focused, but the attention flag and a hidden dock still apply.
        const located = findPane(frame, action.instanceId);
        const hiddenDock =
          located?.location.kind === 'dock' && !frame.docks[located.location.side].visible;
        if (!located?.pane.attention && !hiddenDock) return frame;
      }
      return focusPane(frame, action.instanceId) ?? frame;
    }

    case 'FOCUS_AREA':
      if (frame.focusedAreaId === action.areaId) return frame;
      return focusArea(frame, action.areaId) ?? frame;

    case 'SET_PRESENTED': {
      if (frame.presentedInstanceId === action.instanceId) return frame;
      if (action.instanceId !== null && !findPane(frame, action.instanceId)) return frame;
      return { ...frame, presentedInstanceId: action.instanceId };
    }

    case 'TOGGLE_MAXIMIZED':
      return toggleMaximized(frame, action.areaId);

    case 'SET_SPLIT_SIZES':
      return setSplitSizes(frame, action.splitId, action.sizes);

    case 'SET_DOCK_VISIBLE':
      return setDockVisible(frame, action.side, action.visible);

    case 'SET_DOCK_SIZE':
      return setDockSize(frame, action.side, action.size);

    case 'ARRANGE_CENTER':
      return arrangeCenter(frame, action.style);

    case 'SET_POPOUT_BOUNDS': {
      const popout = frame.popouts.find((p) => p.id === action.popoutId);
      if (!popout) return frame;
      const patch: { bounds?: PopoutBounds; displayId?: number; maximized?: boolean } = {};
      if (action.bounds && !sameBounds(popout.bounds, action.bounds)) patch.bounds = action.bounds;
      if (action.displayId !== undefined && action.displayId !== popout.displayId) {
        patch.displayId = action.displayId;
      }
      if (action.maximized !== undefined && action.maximized !== popout.maximized) {
        patch.maximized = action.maximized;
      }
      return Object.keys(patch).length ? setPopoutBounds(frame, action.popoutId, patch) : frame;
    }

    case 'DOCK_POPOUT':
      return dockPopout(
        frame,
        action.popoutId,
        (pane) => action.targets?.[pane.instanceId] ?? { kind: 'center' },
      );

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

function sameBounds(a: PopoutBounds | null, b: PopoutBounds): boolean {
  return !!a && a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
}
