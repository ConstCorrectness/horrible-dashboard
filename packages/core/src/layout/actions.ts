/**
 * Actions the layout store's reducer understands. These are mechanical layout
 * edits — role routing, region declarations, and singleton rules are resolved
 * by the controller (controller.ts) before dispatch, so the reducer never needs
 * the registry. Everything that mutates bumps `revision` (the autosave dirty
 * check); `LOAD_WORKSPACE` alone replaces state without marking it dirty.
 */
import type {
  AreaSplitDirection,
  BackdropRef,
  DesktopMode,
  DockSide,
  DockState,
  FrameState,
  NavDirection,
  PaneState,
  RegionPosition,
  RegionState,
  SnapZone,
  WindowMode,
  WindowRect,
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
  /**
   * Drag-reorder one tab inside its own area. Purely positional — which pane is
   * showing is preserved across the move (see `reorderTab`).
   */
  | { type: 'REORDER_TAB'; areaId: string; from: number; to: number }
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
  // Windows (the desktop layer)
  //
  // Note what is NOT here: there are no window-tab actions. A window's content is an
  // `AreaNode`, so `SET_ACTIVE_TAB`, `REORDER_TAB`, `INSERT_PANE` and
  // `SET_HEADER_COLLAPSED` above address window tabs by area id already (they resolve
  // through `findAreaAnywhere`). A parallel set would be the same code twice, and the
  // copy would drift.
  /**
   * Pop a pane out of wherever it lives into its own window (the old `FLOAT_PANE`).
   * `rect` is in pixels against `FrameState.windowViewport`; omitted, the window
   * lands on a cascade so an agent that supplied no geometry still gets a usable box.
   */
  | { type: 'WINDOW_FROM_PANE'; instanceId: string; rect?: WindowRect }
  /** Put a window's panes back into a center area (the old `DOCK_FLOATING`). */
  | { type: 'DOCK_WINDOW'; windowId: string; areaId?: string }
  | { type: 'SET_WINDOW_RECT'; windowId: string; rect: WindowRect }
  | { type: 'BRING_WINDOW_FRONT'; windowId: string }
  | { type: 'FOCUS_WINDOW'; windowId: string | null }
  /**
   * Minimize / maximize / restore / snap — deliberately ONE verb, so that
   * `restoreRect` is written and consumed in a single place. Maximizing or snapping
   * stores the current rect; `normal` reads it back and clears it. Minimizing leaves
   * `rect` untouched (restoring returns exactly where it was) and does **not**
   * unmount the pane.
   */
  | {
      type: 'SET_WINDOW_MODE';
      windowId: string;
      mode: WindowMode;
      snap?: SnapZone;
      viewport?: { w: number; h: number };
    }
  /** Titlebar merge: move a pane into another window's tab strip (macOS/Win11). */
  | { type: 'MERGE_INTO_WINDOW'; instanceId: string; windowId: string; index?: number }
  /**
   * The desktop surface was measured or resized: rescale every rect from
   * `windowViewport` to `viewport` and record the new size.
   *
   * This must NOT mark the layout dirty — it is a projection of the same layout onto
   * a different-sized surface, not a user edit. Bumping `revision` here would make
   * every browser resize dirty the workspace and drive the 600ms autosave debounce
   * into a continuous write loop.
   */
  | { type: 'SET_WINDOW_VIEWPORT'; viewport: { w: number; h: number } }
  // Desktop
  /**
   * Flip this desktop between the tiling frame and free windows, rearranging the
   * panes to match (see `explodeToWindows` / `tileWindows`).
   *
   * `dockFor` maps a view id to the dock it belongs in, so that tools going back to a
   * tiling desktop return to their rail instead of landing in the grid. The caller
   * resolves it from the registry and passes it in, because the reducer and the model
   * are deliberately registry-free — the same arrangement `seedFromPreset` uses for
   * `regionsFor`/`dockSizeFor`.
   */
  | {
      type: 'SET_DESKTOP_MODE';
      mode: DesktopMode;
      viewport: { w: number; h: number };
      dockFor?: Record<string, DockSide>;
    }
  | { type: 'SET_BACKDROP'; backdrop: BackdropRef };
