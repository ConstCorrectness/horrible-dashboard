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
  /**
   * Move a pane into a center area from *wherever* it lives — a dock, the
   * floating layer, or another area. The drag-out verb: `MOVE_PANE` only walks
   * the center tree, so it can't carry a docked tool out.
   */
  | { type: 'UNDOCK_PANE_TO_AREA'; instanceId: string; areaId: string }
  | { type: 'SET_ACTIVE_TAB'; areaId: string; index: number }
  /** Swap a pane's view in place (change-pane-type); geometry/instanceId kept. */
  | {
      type: 'SET_PANE_VIEW';
      instanceId: string;
      viewId: string;
      params?: Record<string, unknown>;
      regions?: PaneState['regions'];
    }
  /**
   * Re-point a pane at a new instance id + params in place, so a caller can reuse
   * a pane it already owns (an empty editor buffer) instead of opening a second
   * one. Geometry, tab position, viewId, and region strips are all kept.
   */
  | {
      type: 'RETARGET_PANE';
      instanceId: string;
      newInstanceId: string;
      params?: Record<string, unknown>;
    }
  /** Patch one region strip on a pane (null clears the position). */
  | { type: 'SET_REGION'; instanceId: string; position: RegionPosition; region: RegionState | null }
  /** Switch which in-pane section a pane shows. */
  | { type: 'SET_SECTION'; instanceId: string; section: string }
  // Areas
  | { type: 'SPLIT_AREA'; areaId: string; direction: AreaSplitDirection; pane?: PaneState }
  | { type: 'JOIN_AREA'; areaId: string; direction: NavDirection; adoptTabs?: boolean }
  | { type: 'SET_SPLIT_SIZES'; splitId: string; sizes: number[] }
  /** Target unit-square fractions of the whole center (0..1). */
  | { type: 'RESIZE_AREA'; areaId: string; target: { w?: number; h?: number } }
  | { type: 'FOCUS_AREA'; areaId: string }
  /**
   * Focus a pane instance wherever it lives (null = focus left every pane, e.g.
   * the user clicked the workspace tab strip). When the pane sits in a center
   * area this focuses that area too, so the two stay consistent by construction.
   */
  | { type: 'FOCUS_PANE'; instanceId: string | null }
  | { type: 'SET_FULLSCREEN'; areaId: string | null }
  | { type: 'SET_HEADER_COLLAPSED'; areaId: string; collapsed: boolean }
  // Docks
  | { type: 'SET_DOCK'; side: DockSide; patch: Partial<Pick<DockState, 'visible' | 'size'>> }
  | { type: 'INSERT_TOOL'; side: DockSide; pane: PaneState; activate?: boolean }
  | { type: 'SET_ACTIVE_TOOL'; side: DockSide; instanceId: string }
  /**
   * Move an open pane into a dock from wherever it lives (another dock, a center
   * area, the floating layer) — the rail-customization drop verb. The moved pane
   * becomes the target dock's active tool; the dock's visibility is preserved
   * unless the pane was the visible tool where it came from, in which case the
   * target reveals it (the user was looking at it — keep it on screen).
   */
  | { type: 'MOVE_TOOL'; instanceId: string; side: DockSide }
  /**
   * Resize one docked tool. Writes the tool's own remembered `dockSize` AND the
   * dock's fallback `size`, so the next tool opened on this side inherits the
   * width the user just chose.
   */
  | { type: 'SET_TOOL_SIZE'; side: DockSide; instanceId: string; size: number }
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
