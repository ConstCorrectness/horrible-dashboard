/**
 * Actions the layout store's reducer understands. These are mechanical layout
 * edits — region declarations, singleton rules and window placement are resolved
 * by the controller (controller.ts) before dispatch, so the reducer never needs
 * the registry. Everything that mutates bumps `revision` (the autosave dirty
 * check); `LOAD_WORKSPACE` alone replaces state without marking it dirty.
 */
import type {
  BackdropRef,
  FrameState,
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
  // Panes. `areaId` names a window's area — the tab verbs address windows by it.
  | { type: 'INSERT_PANE'; areaId: string; pane: PaneState; activate?: boolean }
  | { type: 'REMOVE_PANE'; instanceId: string }
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
  /**
   * Replace an open pane's params, keeping its identity and geometry.
   *
   * The missing half of `openPane`. `openPaneRouted` focuses an already-open pane
   * and returned *without applying the params it was handed* — so a deep link
   * ("compare these six runs", "score this checkpoint") worked only when its target
   * pane happened to be closed, and silently did nothing when it was open. Which is
   * the case that matters: these links fire from inside workspaces that seed the
   * target pane.
   *
   * `RETARGET_PANE` cannot serve: it refuses `instanceId === newInstanceId` by
   * design, and a singleton's instance id *is* its view id, so there is no new
   * identity to move to.
   */
  | { type: 'SET_PANE_PARAMS'; instanceId: string; params?: Record<string, unknown> }
  /** Patch one region strip on a pane (null clears the position). */
  | { type: 'SET_REGION'; instanceId: string; position: RegionPosition; region: RegionState | null }
  /** Switch which in-pane section a pane shows. */
  | { type: 'SET_SECTION'; instanceId: string; section: string }
  /**
   * Focus a pane instance (null = focus left every pane, e.g. the user clicked
   * the desktop).
   */
  | { type: 'FOCUS_PANE'; instanceId: string | null }
  /** Present a windowed pane over the whole shell, or clear (`instanceId: null`). */
  | { type: 'SET_PRESENTED'; instanceId: string | null }
  | { type: 'SET_HEADER_COLLAPSED'; areaId: string; collapsed: boolean }
  /**
   * Flag (or clear) a pane as wanting attention — its background work finished.
   * Per pane, not per window: a background tab of a showing window is as out of
   * sight as a minimized window, and either can have a job running.
   */
  | { type: 'SET_PANE_ATTENTION'; instanceId: string; attention: boolean }
  // Windows
  /**
   * Open a new pane in a window of its own. `rect` is in pixels against
   * `FrameState.windowViewport`; omitted, the window lands on a cascade so an agent
   * that supplied no geometry still gets a usable box. Mints three ids — the pane's
   * (when the caller derived it from `paneSeq`), the window's and its area's — so
   * the counter always advances by three.
   */
  | { type: 'OPEN_WINDOW'; pane: PaneState; rect?: WindowRect }
  /**
   * Tear a tab out of its window into a window of its own. Same `rect` rule as
   * `OPEN_WINDOW`. A pane already alone in its window is left where it is.
   */
  | { type: 'WINDOW_FROM_PANE'; instanceId: string; rect?: WindowRect }
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
      /**
       * Snap-assist fill: when this snaps the window to a half, also snap whatever
       * was covering the other half into it, producing a real split.
       *
       * Opt-in rather than always-on, because it moves a window the caller did not
       * name. A user dragging to an edge is asking for a split; an agent tool or a
       * test calling `setWindowMode` is asking for exactly one window to move, and
       * a second one shifting under it would be an effect with no cause on screen.
       */
      fill?: boolean;
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
  | { type: 'SET_BACKDROP'; backdrop: BackdropRef };
