/**
 * Data model for the desktop — the pure, React-free source of truth for
 * everything it shows: its windows, its backdrop, and the per-pane region strips
 * inside them. Rendering lives in packages/ui/src/desktop and
 * packages/ui/src/layout; this layer is plain data + pure functions so it can be
 * unit-tested and driven identically by user gestures, keybindings, and the
 * agent's layout tools. See docs/architecture/windowing.mdx.
 *
 * There is one paradigm. The tiling frame (a Blender-style centre grid, tool
 * docks and activity rails) was removed in 2026-10; a blob saved by it is turned
 * into windows on load by `legacy-frame.ts`.
 */
import type { PaneRole, RegionPosition } from '@horribledashboard/sdk';

export type { PaneRole, RegionPosition };

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
 * A window's content: its tab list. Invariant (enforced by the controller's role
 * routing, not here): `tabs` is either `document` panes, which stack as tabs, or
 * exactly one pane of another role.
 *
 * Named for the centre-grid leaf it used to be, and kept as its own type rather
 * than inlined into `WindowState` because every tab verb (`SET_ACTIVE_TAB`,
 * `REORDER_TAB`, `INSERT_PANE`, `dropPaneOnTab`) addresses a window by its area
 * id.
 */
export interface AreaNode {
  kind: 'area';
  id: string;
  tabs: PaneState[];
  activeTab: number;
  /** Hide the window's tab strip (chrome-less panes). */
  headerCollapsed?: boolean;
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
 * halves, the four corners are quarters, `center` is a centred two-thirds box and
 * `third-*` are full-height vertical thirds. Stored rather than re-derived from the
 * rect, because a window the user dragged to exactly half-width is not snapped and
 * must not spring back to the restore rect when nudged.
 *
 * `center` and the thirds are **keyboard/palette-only** zones: `snapZoneAt` never
 * returns them. A pointer drag has no unambiguous gesture for "the middle third"
 * that would not also change what dragging near an edge means, and the edge/corner
 * gesture is tuned. See `snap.ts`.
 */
export type SnapZone =
  | 'left'
  | 'right'
  | 'top'
  | 'bottom'
  | 'tl'
  | 'tr'
  | 'bl'
  | 'br'
  | 'center'
  | 'third-l'
  | 'third-c'
  | 'third-r'
  | 'max';

/**
 * One OS-style window on the desktop.
 *
 * Its content is an **`AreaNode`**. That is the whole trick behind "one pane per
 * window, tabs optional": a plain window is an area with one tab, a merged window is
 * an area with several, and the tab verbs (`dropPaneOnTab`, `SET_ACTIVE_TAB`,
 * `REORDER_TAB`) address it by area id.
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
  /**
   * What this window was before it was minimized, so restoring returns to it.
   *
   * `WindowMode` is a single enum, so minimizing a maximized or snapped window
   * would otherwise overwrite the only record that it was maximized — and the
   * restore path, which consumes `restoreRect`, would bring it back at its old
   * free-floating size. A mode plus a zone, never a rect: the rect is re-derived
   * from the zone so a restore after an app resize still fills the new surface.
   */
  minimizedFrom?: { mode: 'normal' | 'maximized'; snap?: SnapZone };
  z: number;
}

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
 *
 * It is `splash` — the home surface — and that is a product decision, not a
 * cosmetic one. `HomeView` (avatar, ask bar, connector tiles, first-run setup
 * card) is rendered by exactly one thing in the app: the `splash` backdrop. With
 * a decorative default, the entire tier-1 entry surface was unreachable on a
 * clean install: the app opened on a wallpaper and a taskbar, and the person the
 * home view was designed for — someone who never opens a terminal — met an empty
 * screen. A backdrop that is only decoration must never be the default while one
 * of them carries the front door.
 */
export const DEFAULT_BACKDROP = 'splash';

/** Everything the desktop shows. Serialized as the workspace's layout blob. */
export interface FrameState {
  /** The windows on this desktop, in no particular order — `z` is the stacking. */
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
  /** What paints behind the windows. */
  backdrop: BackdropRef;
  /**
   * A windowed pane instance presented over the entire shell — past the taskbar,
   * which a maximized window deliberately stops short of. Null unless something
   * is presented.
   *
   * Deliberately **not serialized**. It is a way of looking at a pane for a
   * moment, not a property of the desktop; restoring a saved layout into a
   * full-screen pane with the chrome hidden is a state the user has to guess
   * their way out of.
   */
  presentedInstanceId: string | null;
  /**
   * The focused pane **instance** — the keyboard's idea of "where you are". The
   * window and location are derived from it (`findPaneAnywhere`) rather than
   * stored, so they cannot drift.
   */
  focusedInstanceId: string | null;
  /**
   * The focused **window**, for the keyboard's window verbs (snap, minimize, cycle).
   * Distinct from `focusedInstanceId`, which names a pane: a window verb acts on the
   * whole window, including the tabs that aren't showing.
   */
  focusedWindowId: string | null;
  /** Monotonic id counter for pane instances, windows and their areas. Never reset. */
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

/**
 * Where a pane instance currently lives: always a window. `areaId` is the window's
 * own area, which the tab verbs address. Still a tagged object rather than a bare
 * window id, so a caller's `location.windowId` reads the same as it always has.
 */
export interface PaneLocation {
  kind: 'window';
  windowId: string;
  areaId: string;
}

/** A pane plus its location, as returned by `listPanes`. */
export interface LocatedPane {
  pane: PaneState;
  location: PaneLocation;
}

/** Directions for focus-move / neighbor queries (viewport axes). */
export type NavDirection = 'left' | 'right' | 'up' | 'down';
