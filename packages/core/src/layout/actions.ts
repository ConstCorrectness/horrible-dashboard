/**
 * Actions the layout store's reducer understands. These are mechanical layout
 * edits — role routing, region declarations, and singleton rules are resolved
 * by the controller (controller.ts) before dispatch, so the reducer never needs
 * the registry. Everything that mutates bumps `revision` (the autosave dirty
 * check); `LOAD_WORKSPACE` alone replaces state without marking it dirty.
 */
import type {
  AreaSplitDirection,
  DockSide,
  DockState,
  FrameState,
  NavDirection,
  PaneState,
  RegionPosition,
  RegionState,
} from './types';

export type LayoutAction =
  /** Atomically swap in a workspace (id + frame together — never separately). */
  | { type: 'LOAD_WORKSPACE'; workspaceId: string; frame: FrameState }
  // Center-grid panes
  | { type: 'INSERT_PANE'; areaId: string; pane: PaneState; activate?: boolean }
  | { type: 'REMOVE_PANE'; instanceId: string }
  | { type: 'MOVE_PANE'; instanceId: string; targetAreaId: string }
  | { type: 'SET_ACTIVE_TAB'; areaId: string; index: number }
  /** Swap a pane's view in place (change-pane-type); geometry/instanceId kept. */
  | {
      type: 'SET_PANE_VIEW';
      instanceId: string;
      viewId: string;
      params?: Record<string, unknown>;
      regions?: PaneState['regions'];
    }
  /** Patch one region strip on a pane (null clears the position). */
  | { type: 'SET_REGION'; instanceId: string; position: RegionPosition; region: RegionState | null }
  // Areas
  | { type: 'SPLIT_AREA'; areaId: string; direction: AreaSplitDirection; pane?: PaneState }
  | { type: 'JOIN_AREA'; areaId: string; direction: NavDirection; adoptTabs?: boolean }
  | { type: 'SET_SPLIT_SIZES'; splitId: string; sizes: number[] }
  /** Target unit-square fractions of the whole center (0..1). */
  | { type: 'RESIZE_AREA'; areaId: string; target: { w?: number; h?: number } }
  | { type: 'FOCUS_AREA'; areaId: string }
  | { type: 'SET_FULLSCREEN'; areaId: string | null }
  | { type: 'SET_HEADER_COLLAPSED'; areaId: string; collapsed: boolean }
  // Docks
  | { type: 'SET_DOCK'; side: DockSide; patch: Partial<Pick<DockState, 'visible' | 'size'>> }
  | { type: 'INSERT_TOOL'; side: DockSide; pane: PaneState; activate?: boolean }
  | { type: 'SET_ACTIVE_TOOL'; side: DockSide; instanceId: string }
  // Floating layer
  | {
      type: 'FLOAT_PANE';
      instanceId: string;
      rect?: { x: number; y: number; w: number; h: number };
    }
  | { type: 'DOCK_FLOATING'; instanceId: string; areaId?: string }
  | {
      type: 'SET_FLOATING_RECT';
      instanceId: string;
      rect: { x: number; y: number; w: number; h: number };
    }
  | { type: 'BRING_FLOATING_FRONT'; instanceId: string };
