/**
 * Turning a floating desktop (frame v3) into a docked workbench (v4).
 *
 * The counterpart of `legacy-frame.ts`, one generation on: that file turns the
 * old tiling blobs into windows, this one turns windows into docks, so every
 * saved layout still loads by running the steps in order.
 *
 * The rule is that no pane is closed. Geometry cannot survive — a workbench has
 * no free rects — so what is kept is the intent the geometry expressed:
 *
 * - **Documents** become tabs in the centre. A window snapped to the left or
 *   right half (or third, or quarter) was a deliberate side-by-side, so it seeds
 *   a column; everything else joins the main group.
 * - **Tools and widgets** go to an edge dock: the side the view asks for, else
 *   the side the window's own shape and position suggest (wide and short was a
 *   bottom panel; otherwise the half of the screen it sat on).
 * - **Minimized** windows become background tabs, which is what they were:
 *   mounted, one click away, not on screen. A dock that held only minimized
 *   windows starts hidden.
 *
 * Pure and registry-free: the caller supplies the role lookup.
 */
import { areaId, createArea } from '../model';
import type { AreaNode, FrameState, PaneRole, PaneState, SnapZone, WindowState } from '../types';
import { DEFAULT_DOCK_SIZES, MIN_DOCK_SIZE, sanitize } from './frame';
import { splitId, withTab } from './tree';
import { DOCK_SIDES } from './types';
import type { DockFrame, DockSide, DockState, LayoutNode } from './types';

export interface MigrateOptions {
  /** A view's role, or undefined when its module is not loaded. */
  roleOf: (viewId: string) => PaneRole | undefined;
  /** A view's declared dock side, when it has one. */
  dockOf?: (viewId: string) => DockSide | undefined;
}

type Column = 'left' | 'main' | 'right';

const COLUMN_OF_SNAP: Partial<Record<SnapZone, Column>> = {
  left: 'left',
  'third-l': 'left',
  tl: 'left',
  bl: 'left',
  right: 'right',
  'third-r': 'right',
  tr: 'right',
  br: 'right',
};

/** A tool window this much wider than tall was being used as a bottom panel. */
const BOTTOM_ASPECT = 1.6;
const MAX_SIDE_DOCK = 520;
const MAX_BOTTOM_DOCK = 420;

/** The dock a tool window's shape and position suggest. */
function dockFromShape(win: WindowState, viewport: FrameState['windowViewport']): DockSide {
  const { rect } = win;
  if (rect.h > 0 && rect.w / rect.h >= BOTTOM_ASPECT) return 'bottom';
  if (!viewport || viewport.w <= 0) return 'right';
  return rect.x + rect.w / 2 < viewport.w / 2 ? 'left' : 'right';
}

interface Slot {
  area: AreaNode;
  /** Highest `z` among the shown windows that fed this slot, or -1 for none. */
  shownZ: number;
  /** The shown window that set the slot's active tab, for dock sizing. */
  shownBy: WindowState | null;
}

export function dockFrameFromWindows(frame: FrameState, opts: MigrateOptions): DockFrame {
  let seq = frame.paneSeq;
  const mint = (): Slot => ({ area: createArea(areaId(seq++)), shownZ: -1, shownBy: null });

  const columns: Record<Column, Slot> = { left: mint(), main: mint(), right: mint() };
  const docks: Record<DockSide, Slot> = { left: mint(), right: mint(), bottom: mint() };

  const add = (slot: Slot, pane: PaneState, win: WindowState, shown: boolean): void => {
    // Topmost shown window wins the active tab; everything else lands behind it.
    const activate = shown && win.z > slot.shownZ;
    slot.area = withTab(slot.area, pane, { activate });
    if (activate) {
      slot.shownZ = win.z;
      slot.shownBy = win;
    }
  };

  // Store order, which is creation order: tabs come out in the order the user
  // opened things. `z` only decides which tab is in front.
  for (const win of frame.windows) {
    const minimized = win.mode === 'minimized';
    win.area.tabs.forEach((pane, index) => {
      const shown = !minimized && index === win.area.activeTab;
      const role = opts.roleOf(pane.viewId);
      if (role === 'tool' || role === 'widget') {
        const side =
          opts.dockOf?.(pane.viewId) ??
          (role === 'widget' ? 'right' : dockFromShape(win, frame.windowViewport));
        add(docks[side], pane, win, shown);
        return;
      }
      // Documents, and views whose module is not loaded: those render a
      // placeholder with an Enable button, which belongs where documents go.
      const column =
        !minimized && win.mode === 'normal' && win.snap
          ? (COLUMN_OF_SNAP[win.snap] ?? 'main')
          : 'main';
      add(columns[column], pane, win, shown);
    });
  }

  const used = (['left', 'main', 'right'] as const)
    .map((c) => columns[c].area)
    .filter((area) => area.tabs.length > 0);
  const center: LayoutNode =
    used.length === 0
      ? columns.main.area
      : used.length === 1
        ? used[0]
        : {
            kind: 'split',
            id: splitId(seq++),
            orientation: 'row',
            children: used,
            sizes: used.map(() => 1 / used.length),
          };

  const dockState = (side: DockSide): DockState => {
    const slot = docks[side];
    const rect = slot.shownBy?.rect;
    const wished = rect ? (side === 'bottom' ? rect.h : rect.w) : DEFAULT_DOCK_SIZES[side];
    const cap = side === 'bottom' ? MAX_BOTTOM_DOCK : MAX_SIDE_DOCK;
    return {
      visible: slot.shownBy !== null,
      size: Math.round(Math.min(Math.max(wished, MIN_DOCK_SIZE), cap)),
      area: slot.area,
    };
  };

  const next: DockFrame = {
    center,
    docks: Object.fromEntries(DOCK_SIDES.map((side) => [side, dockState(side)])) as Record<
      DockSide,
      DockState
    >,
    popouts: [],
    backdrop: frame.backdrop,
    maximizedAreaId: null,
    presentedInstanceId: null,
    focusedInstanceId: frame.focusedInstanceId,
    focusedAreaId: null,
    paneSeq: seq,
  };
  return sanitize(next);
}
