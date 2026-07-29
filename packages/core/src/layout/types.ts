/**
 * Data model for the frame layout engine — the pure, React-free source of truth
 * for everything the workspace shows: the Blender-style center area grid, the
 * three fixed tool docks, the floating layer, fullscreen, and per-pane region
 * strips. Rendering lives in packages/ui/src/layout; this layer is plain data +
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
   * For a pane docked as a tool: the dock extent it was last dragged to, in px.
   * Remembered per tool rather than per dock so two tools sharing a side (a
   * narrow file tree and a wide agent chat) don't fight over one width. Absent
   * until the user resizes — the dock's own `size` is the fallback.
   */
  dockSize?: number;
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

/** A lightweight in-window floating pane (not an OS window). */
export interface FloatingPane {
  pane: PaneState;
  /** Fractions of the center grid's bounds, so rects survive resizes/DPI. */
  rect: { x: number; y: number; w: number; h: number };
  z: number;
}

/** Everything one workspace shows. Serialized as the workspace's layout blob. */
export interface FrameState {
  center: LayoutNode;
  docks: Record<DockSide, DockState>;
  floating: FloatingPane[];
  /** Area temporarily filling the whole frame (Blender ctrl+space), or null. */
  fullscreenAreaId: string | null;
  /** Focused center area — area verbs (split/join/nav) and role routing target it. */
  focusedAreaId: string | null;
  /**
   * The focused pane **instance**, wherever it lives — a center tab, a docked
   * tool, or a floating pane. This is the keyboard's idea of "where you are":
   * `focusedAreaId` only ever names a center area, so before this existed,
   * clicking a docked tool left every pane-scoped command pointed at whatever
   * center area was focused last. The view id and location are derived from it
   * (`findPaneAnywhere`) rather than stored, so they cannot drift.
   */
  focusedInstanceId: string | null;
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
  | { kind: 'floating' };

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
