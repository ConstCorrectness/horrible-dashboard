/**
 * Data model for the frame layout engine — the pure, React-free source of truth
 * for everything the workspace shows: the Blender-style center area grid, the
 * three fixed tool docks, the desktop's windows and backdrop, fullscreen, and
 * per-pane region strips. Rendering lives in packages/ui/src/layout and
 * packages/ui/src/desktop; this layer is plain data +
 * pure functions so it can be unit-tested and driven identically by user
 * gestures, keybindings, and the agent's layout tools. See
 * docs/architecture/windowing.mdx.
 */
import type { DockSide, PaneRole, RegionPosition } from '@horribledashboard/sdk';

export type { DockSide, PaneRole, RegionPosition };

/**
 * Live state of one region strip (Blender N/T-panel style) on a pane instance.
 * Persisted with the layout, so strips reopen exactly as left — per instance,
 * not per view (each editor buffer keeps its own).
 */
export interface RegionState {
  open: boolean;
  /** Strip size in px (width for left/right, height for bottom). */
  size: number;
  /** Blender-style tuck: open but hidden behind a thin rail. */
  collapsed: boolean;
  /** View ids stacked at this position, in declaration order. */
  views: string[];
  activeView: string;
}

/** One open pane instance — a view id plus its per-instance state. */
export interface PaneState {
  /** Unique per workspace, `${viewId}#${n}` (n from `FrameState.paneSeq`). */
  instanceId: string;
  viewId: string;
  params?: Record<string, unknown>;
  /** Region strips keyed by position. Absent = the view declares no regions. */
  regions?: Partial<Record<RegionPosition, RegionState>>;
  /**
   * Active in-pane section id. Persisted **per instance**, like `regions` and for
   * the same reason: two panes of one view are two places, and the module
   * singletons this replaces (games' `hub-section`, the client drawer) forgot the
   * user's choice on every reload. Absent = the view declares no sections.
   */
  activeSection?: string;
  /**
   * For a pane docked as a tool: the dock extent it was last dragged to, in px.
   * Remembered per tool rather than per dock so two tools sharing a side (a
   * narrow file tree and a wide agent chat) don't fight over one width. Absent
   * until the user resizes — the dock's own `size` is the fallback.
   */
  dockSize?: number;
  /**
   * Minimized: open, listed in the taskbar, but not showing.
   *
   * A *window* has its own `mode: 'minimized'`, so this flag is only ever set on
   * a pane in a centre area — which is precisely where minimizing used to be
   * impossible, because the taskbar's one verb could hide a window and nothing
   * else, and most workspaces are tiling.
   *
   * It is a flag rather than a parking list on the frame, so the pane stays in
   * its area at its index: the split geometry, the tab order and the area itself
   * all survive, and restoring puts the pane back exactly where it was. Pulling
   * it out of the tree instead would collapse a single-pane area and there is no
   * verb that can rebuild a split (see `move_pane` in the layout tools).
   */
  minimized?: boolean;
  /**
   * The pane wants looking at: a long job it was running has finished.
   *
   * The counterpart to minimizing being non-destructive. Once a pane can be put
   * away while it keeps working — a deep-research run, a training job, a
   * compile, an ingest — there has to be a way for it to say it is done, or
   * "minimize it and get on with something else" means polling it by hand.
   *
   * Set by the pane through `requestPaneAttention`, cleared the moment the user
   * actually looks at it. Deliberately a boolean and not a count: the taskbar
   * button is a flash, not an inbox, and "3 things happened" is a story the pane
   * itself should tell once you are in it.
   */
  attention?: boolean;
}

/**
 * A leaf of the center grid. Invariant (enforced by the controller's role
 * routing, not the tree): `tabs` is either all `document` panes (rendered with a
 * tab strip), exactly one `widget` pane (no strip), or empty (view picker).
 */
export interface AreaNode {
  kind: 'area';
  id: string;
  tabs: PaneState[];
  activeTab: number;
  /** Hide the area header (chrome-less dashboards). */
  headerCollapsed?: boolean;
}

/** An interior node of the center grid: children side by side (`row`) or stacked (`column`). */
export interface SplitNode {
  kind: 'split';
  id: string;
  orientation: 'row' | 'column';
  /** Always ≥ 2 after `normalize`. */
  children: LayoutNode[];
  /** Fractions of this split's extent, same length as `children`, sum 1. */
  sizes: number[];
}

export type LayoutNode = SplitNode | AreaNode;

/** One fixed tool dock. Tools stack like an activity-bar list; one is visible. */
export interface DockState {
  visible: boolean;
  /**
   * Px width (left/right) or height (bottom) — the dock's fallback extent, used
   * for tools that have no remembered `dockSize` of their own. Tracks the last
   * size the user dragged on this side, so a freshly opened tool lands near where
   * they left things rather than snapping back to the built-in default.
   */
  size: number;
  /** Role `tool` panes only. */
  tools: PaneState[];
  /** Instance id of the visible tool, or null when empty. */
  activeTool: string | null;
}

/** A window's position and size, in pixels within the desktop surface. */
export interface WindowRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * A window's own state, independent of where it sits. `minimized` keeps the pane
 * **mounted** (a minimized terminal is still running — see
 * docs/architecture/desktop-shell.mdx); only the chrome is hidden.
 */
export type WindowMode = 'normal' | 'minimized' | 'maximized';

/**
 * Where a window has been snapped. `max` is the whole surface; the four edges are
 * halves and the four corners are quarters. Stored rather than re-derived from the
 * rect, because a window the user dragged to exactly half-width is not snapped and
 * must not spring back to the restore rect when nudged.
 */
export type SnapZone = 'left' | 'right' | 'top' | 'bottom' | 'tl' | 'tr' | 'bl' | 'br' | 'max';

/**
 * One OS-style window on the desktop.
 *
 * Its content is an **`AreaNode` — the same node type the center grid uses**. That is
 * the whole trick behind "one pane per window, tabs optional": a plain window is an
 * area with one tab, a merged window is an area with several, and every piece of tab
 * machinery that already exists (the tab strip in `AreaHeader`, `dropPaneOnTab`,
 * `SET_ACTIVE_TAB`, `REORDER_TAB`, the role invariant) applies to windows unchanged
 * instead of being duplicated in a parallel vocabulary.
 *
 * Geometry is **pixels**, against `FrameState.windowViewport` — the surface size the
 * rects were last measured on. Fractions survive a resize but cannot express a minimum
 * size and reflow a window's contents on every drag of the app edge; pixels plus a
 * remembered viewport let a layout be rescaled proportionally when it is opened at a
 * different size, while staying pixel-stable during ordinary use.
 */
export interface WindowState {
  /** Unique per workspace, `w<n>` (n from `FrameState.paneSeq`). */
  id: string;
  area: AreaNode;
  rect: WindowRect;
  /** Geometry to return to when un-maximizing or un-snapping. */
  restoreRect?: WindowRect;
  mode: WindowMode;
  snap?: SnapZone;
  z: number;
}

/** Which paradigm a desktop runs: free windows, or the tiling frame engine. */
export type DesktopMode = 'floating' | 'tiling';

/** The backdrop a desktop renders behind its windows (a registered provider id). */
export interface BackdropRef {
  id: string;
  params?: Record<string, unknown>;
}

/**
 * The backdrop a desktop gets when it has never been told otherwise — a fresh
 * frame, and a v1 blob being migrated.
 *
 * A migrated desktop deliberately does **not** get `none`. The rest of the
 * migration preserves what the user had, but there is nothing to preserve here:
 * before v2 a desktop had no backdrop at all, because there was no surface it
 * could show on. Choosing `none` would not be conservative, it would hand every
 * existing user a blank landing screen for a feature they never turned off.
 *
 * A model-layer constant, not the registry's: the reducer and the deserializer
 * must be able to name it without knowing which providers are registered — and
 * an unregistered id already falls back gracefully at render time.
 */
export const DEFAULT_BACKDROP = 'aurora';

/** Everything one workspace shows. Serialized as the workspace's layout blob. */
export interface FrameState {
  center: LayoutNode;
  docks: Record<DockSide, DockState>;
  /**
   * The windows on this desktop. Present in **both** modes: a floating window on a
   * tiling desktop is the deliberate escape hatch (i3's floating layer), so the
   * center tree and the window list always coexist.
   */
  windows: WindowState[];
  /**
   * The desktop-surface size `windows[].rect` was last measured against, or null
   * before anything has measured it. Rects are rescaled through this when the
   * surface turns out to be a different size than when they were saved (a second
   * monitor, a resized app, a phone). Migrated v1 blobs arrive as `{w:1,h:1}`, which
   * makes their old fractional rects rescale into pixels through the ordinary path
   * with no special case.
   */
  windowViewport: { w: number; h: number } | null;
  /** This desktop's paradigm. Rails and docks render only when `tiling`. */
  mode: DesktopMode;
  /** What paints behind the windows. */
  backdrop: BackdropRef;
  /** Area temporarily filling the whole frame (Blender ctrl+space), or null. */
  fullscreenAreaId: string | null;
  /**
   * A **windowed** pane instance presented over the entire shell — past the
   * workspace strip and the taskbar, which a maximized window deliberately stops
   * short of. Null unless something is presented.
   *
   * The floating-mode counterpart of `fullscreenAreaId`, and separate from it
   * because the two name different things: an area in the centre tree versus a
   * pane instance in a window, validated against different halves of the state.
   * `presentPane` picks whichever applies, so callers use one verb.
   *
   * Deliberately **not serialized**. It is a way of looking at a pane for a
   * moment, not a property of the workspace; restoring a saved layout into a
   * full-screen pane with the chrome hidden is a state the user has to guess
   * their way out of.
   */
  presentedInstanceId: string | null;
  /** Focused center area — area verbs (split/join/nav) and role routing target it. */
  focusedAreaId: string | null;
  /**
   * The focused pane **instance**, wherever it lives — a center tab, a docked
   * tool, or a window tab. This is the keyboard's idea of "where you are":
   * `focusedAreaId` only ever names a center area, so before this existed,
   * clicking a docked tool left every pane-scoped command pointed at whatever
   * center area was focused last. The view id and location are derived from it
   * (`findPaneAnywhere`) rather than stored, so they cannot drift.
   */
  focusedInstanceId: string | null;
  /**
   * The focused **window**, for the keyboard's window verbs (snap, minimize, cycle).
   * Null means focus is in the tiled frame or a dock. Distinct from
   * `focusedInstanceId`, which names a pane: a window verb acts on the whole window,
   * including the tabs that aren't showing.
   */
  focusedWindowId: string | null;
  /** Monotonic id counter for pane instances and tree nodes. Never reset. */
  paneSeq: number;
}

/**
 * The layout store's full snapshot. `workspaceId` and `frame` live in ONE atom
 * so autosave can never write a layout under the wrong workspace id — loading a
 * workspace replaces both atomically (this is what retires the old engine's
 * race-guard refs).
 */
export interface LayoutStoreState {
  workspaceId: string | null;
  frame: FrameState;
  /** False until the first LOAD_WORKSPACE; autosave is gated on it. */
  hydrated: boolean;
  /** Bumped by every user mutation, NOT by loads — the autosave dirty check. */
  revision: number;
}

/** Unit-square bounds of an area within the center grid (for neighbor queries). */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Where a pane instance currently lives. */
export type PaneLocation =
  | { kind: 'area'; areaId: string }
  | { kind: 'dock'; dock: DockSide }
  /** `areaId` is the window's own area, so area verbs work on window tabs too. */
  | { kind: 'window'; windowId: string; areaId: string };

/** A pane plus its location, as returned by `listPanes`. */
export interface LocatedPane {
  pane: PaneState;
  location: PaneLocation;
}

/** Directions for focus-move / neighbor queries (viewport axes, not tree axes). */
export type NavDirection = 'left' | 'right' | 'up' | 'down';

/**
 * Directions an area can be split toward. Mirrors the registry's
 * `SplitDirection` (kept literal here so this layer stays dependency-free):
 * `left`/`right` produce a `row` split, `above`/`below` a `column` one.
 */
export type AreaSplitDirection = 'left' | 'right' | 'above' | 'below';
