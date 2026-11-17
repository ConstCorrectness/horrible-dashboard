/**
 * Data model for the docked workbench — the layout that replaces the floating
 * desktop (frame schema v4).
 *
 * One paradigm, like the desktop it replaces: a centre split tree of tab groups,
 * three edge docks, and (on desktop) groups popped out into OS windows. There is
 * no floating layer and no mode switch; the escape hatches are maximizing a group,
 * presenting a pane, and popping a group out.
 *
 * `FrameState` is an alias for `DockFrame`. The v3 window model stays in
 * `../types.ts` only so older blobs can be read; `migrate.ts` is the bridge.
 * See docs/architecture/windowing.mdx.
 */
import type { AreaNode, BackdropRef } from '../types';

/** The three edges a tool can dock to. There is no top dock: the title bar is there. */
export type DockSide = 'left' | 'right' | 'bottom';

export const DOCK_SIDES: readonly DockSide[] = ['left', 'right', 'bottom'];

/** An interior node of the centre: children side by side (`row`) or stacked (`column`). */
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

/**
 * One edge dock.
 *
 * Its content is an `AreaNode`, the same tab group the centre uses. The tiling
 * frame this descends from gave docks their own `tools[]`/`activeTool` and with
 * them a second set of verbs that drifted from the tab verbs; one node type means
 * `SET_ACTIVE_TAB`, reorder, insert and drop work on a dock unchanged.
 */
export interface DockState {
  visible: boolean;
  /** Px width (left/right) or height (bottom). Clamped to the surface when drawn. */
  size: number;
  area: AreaNode;
}

/** Screen-space bounds of an OS window, in the shell's coordinates. */
export interface PopoutBounds {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A tab group living in its own OS window (desktop only). */
export interface PopoutState {
  /** Unique per workspace, `p<n>` (n from `DockFrame.paneSeq`). */
  id: string;
  area: AreaNode;
  /** Null until the window has reported where it opened. */
  bounds: PopoutBounds | null;
  /** The display the bounds were measured on, for restore on a changed monitor set. */
  displayId?: number;
  maximized: boolean;
}

/** Everything the workbench shows. Serialized as the workspace's layout blob. */
export interface DockFrame {
  /** Never an empty tree: with nothing open it is one empty area, which shows Home. */
  center: LayoutNode;
  docks: Record<DockSide, DockState>;
  popouts: PopoutState[];
  backdrop: BackdropRef;
  /**
   * A centre or dock group drawn over the whole workbench. Not serialized, for the
   * reason `presentedInstanceId` is not: it is a way of looking, not a layout.
   */
  maximizedAreaId: string | null;
  /** A pane presented over the entire shell, past the status bar. Not serialized. */
  presentedInstanceId: string | null;
  focusedInstanceId: string | null;
  /**
   * The focused **group**. Stored as well as the instance because an empty group
   * can hold focus (it is where the next document opens) and has no instance.
   */
  focusedAreaId: string | null;
  /** Monotonic id counter for panes, areas, splits and popouts. Never reset. */
  paneSeq: number;
}

/** Where a tab group lives. */
export type AreaHost =
  | { kind: 'center' }
  | { kind: 'dock'; side: DockSide }
  | { kind: 'popout'; popoutId: string };

/** Where a pane instance lives: its group, and what hosts the group. */
export type DockLocation = AreaHost & { areaId: string };

/** Which side of a group a split opens on. */
export type SplitSide = 'left' | 'right' | 'above' | 'below';

/** Where to put a pane. */
export type PlaceTarget =
  /** Into a specific group, at `index` or the end. */
  | { kind: 'area'; areaId: string; index?: number }
  /** Into the focused centre group. */
  | { kind: 'center' }
  | { kind: 'dock'; side: DockSide }
  /** Into a new centre group beside `areaId`. */
  | { kind: 'split'; areaId: string; side: SplitSide }
  /** Into a new OS window. */
  | { kind: 'popout'; bounds?: PopoutBounds };

/** Unit-square or pixel rect, depending on the function that returns it. */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
