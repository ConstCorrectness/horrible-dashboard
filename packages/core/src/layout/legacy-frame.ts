/**
 * Turning a layout saved by the tiling frame into windows.
 *
 * Until 2026-10 a desktop could be **tiling** — a Blender-style centre grid of split
 * areas plus three tool docks — or **floating**. Tiling is gone, but the blobs it
 * wrote are not: every saved layout carries a `center` tree and `docks`, and on a
 * floating desktop those still held panes (the grid was retained, just not drawn).
 * Dropping them would close the user's panes on upgrade, so they become windows.
 *
 * - A **tiling** desktop's grid becomes one window per non-empty area, at the rect
 *   that area occupied, so the first load after the upgrade looks like the last
 *   load before it. A visible dock's active tool becomes a window at the dock's
 *   geometry; every other docked tool becomes a minimized window, which is what a
 *   tool behind a rail glyph was: mounted, one click away, not on screen.
 * - A **floating** desktop's grid and docks were never on screen, and the taskbar
 *   already reported their panes as minimized. They stay minimized.
 *
 * Pure and registry-free like `serialize.ts`, which calls it; pane validation is the
 * caller's `readPane`, so a retired view is pruned here by exactly the same rule as
 * everywhere else in a blob.
 */
import { areaId, windowId } from './model';
import { NOMINAL_VIEWPORT } from './snap';
import type { PaneState, WindowRect, WindowState } from './types';

type Raw = Record<string, unknown>;
type Size = { w: number; h: number };

const DOCK_SIDES = ['left', 'right', 'bottom'] as const;
/** The old docks' default extents, for a blob that never recorded one. */
const DOCK_DEFAULTS: Record<(typeof DOCK_SIDES)[number], number> = {
  left: 280,
  right: 320,
  bottom: 240,
};

interface LegacyArea {
  tabs: PaneState[];
  activeTab: number;
  headerCollapsed: boolean;
  /**
   * Every surviving tab carried the old per-pane `minimized` flag, so the area was
   * showing nothing. Read off the raw tabs: `PaneState` no longer has the field.
   */
  minimized: boolean;
  /** Fractions of the whole grid. */
  unit: WindowRect;
}

/** Walk the old split tree, computing each area's unit-square rect as it goes. */
function collect(node: unknown, unit: WindowRect, readPane: (v: unknown) => PaneState | null) {
  const out: LegacyArea[] = [];
  const walk = (value: unknown, rect: WindowRect): void => {
    if (!value || typeof value !== 'object') return;
    const n = value as Raw;
    if (n.kind === 'area') {
      let minimized = true;
      const tabs = (Array.isArray(n.tabs) ? n.tabs : []).flatMap((raw: unknown) => {
        const pane = readPane(raw);
        if (!pane) return [];
        if ((raw as Raw).minimized !== true) minimized = false;
        return [pane];
      });
      if (tabs.length === 0) return;
      const active = typeof n.activeTab === 'number' ? n.activeTab : 0;
      out.push({
        tabs,
        activeTab: Math.min(Math.max(active, 0), tabs.length - 1),
        headerCollapsed: n.headerCollapsed === true,
        minimized,
        unit: rect,
      });
      return;
    }
    if (n.kind !== 'split' || !Array.isArray(n.children) || n.children.length === 0) return;
    const children = n.children;
    const raw = children.map((_, i) => {
      const s = Array.isArray(n.sizes) ? (n.sizes as unknown[])[i] : undefined;
      return typeof s === 'number' && s > 0 ? s : 1;
    });
    const total = raw.reduce((a, b) => a + b, 0);
    let offset = 0;
    children.forEach((child, i) => {
      const f = raw[i] / total;
      walk(
        child,
        n.orientation === 'column'
          ? { x: rect.x, y: rect.y + offset * rect.h, w: rect.w, h: f * rect.h }
          : { x: rect.x + offset * rect.w, y: rect.y, w: f * rect.w, h: rect.h },
      );
      offset += f;
    });
  };
  walk(node, unit);
  return out;
}

/**
 * A unit-square rect scaled onto the surface.
 *
 * The surface may itself be the unit square: a v1 blob's rects are fractions until
 * the first measure, which scales them all up together. Whole pixels there would
 * round every rect to 0 or 1, so fractions keep three decimals instead. Nothing here
 * clamps either — the pixel helpers in `snap.ts` would produce nonsense on that
 * basis, and `SET_WINDOW_VIEWPORT` clamps every rect once it knows the real size.
 */
function onSurface(unit: WindowRect, surface: Size): WindowRect {
  const pixels = surface.w > 1 || surface.h > 1;
  const scale = (v: number, of: number) =>
    pixels ? Math.round(v * of) : Math.round(v * of * 1000) / 1000;
  return {
    x: scale(unit.x, surface.w),
    y: scale(unit.y, surface.h),
    w: scale(unit.w, surface.w),
    h: scale(unit.h, surface.h),
  };
}

type DockSide = (typeof DOCK_SIDES)[number];

/** A dock's extent as a fraction of the surface. Dock sizes are pixels; on a unit
 * basis they are taken as fractions of the nominal viewport, the only size there is
 * to divide by. */
function dockFraction(side: DockSide, size: number, surface: Size): number {
  const unitBasis = surface.w <= 1 && surface.h <= 1;
  const of =
    side === 'bottom'
      ? unitBasis
        ? NOMINAL_VIEWPORT.h
        : surface.h
      : unitBasis
        ? NOMINAL_VIEWPORT.w
        : surface.w;
  return Math.min(size / of, 1);
}

/** The visible docks' extents. The old frame ran the side docks full height, sat the
 * bottom dock between them and gave the centre grid what was left. */
interface Insets {
  left: number;
  right: number;
  bottom: number;
}

/** Where a docked tool sat, given the extents of the docks that were showing. */
function dockRect(side: DockSide, fraction: number, insets: Insets, surface: Size): WindowRect {
  const unit =
    side === 'bottom'
      ? { x: insets.left, y: 1 - fraction, w: 1 - insets.left - insets.right, h: fraction }
      : side === 'left'
        ? { x: 0, y: 0, w: fraction, h: 1 }
        : { x: 1 - fraction, y: 0, w: fraction, h: 1 };
  return onSurface(unit, surface);
}

/** A centre-grid rect (a fraction of the grid) as a fraction of the whole surface. */
function inGrid(unit: WindowRect, insets: Insets): WindowRect {
  const w = 1 - insets.left - insets.right;
  const h = 1 - insets.bottom;
  return { x: insets.left + unit.x * w, y: unit.y * h, w: unit.w * w, h: unit.h * h };
}

/**
 * Where a migrated window that was not on screen comes back to when restored: a
 * cascade, like the one new windows open on.
 */
function hiddenRect(index: number, surface: Size): WindowRect {
  const at = 0.08 + (index % 8) * 0.03;
  return onSurface({ x: at, y: at, w: 0.52, h: 0.58 }, surface);
}

/**
 * The windows a pre-v3 blob's `center` and `docks` turn into, stacked beneath the
 * windows it already had (a tiling desktop's own windows floated above its grid).
 * Rects are in the blob's own basis and are not clamped — see `hiddenRect`.
 *
 * `seq` is the first free id number; ids are minted from it two at a time (window,
 * area), and the next free number is returned with the windows.
 */
export function windowsFromLegacyFrame(
  src: Raw,
  readPane: (value: unknown) => PaneState | null,
  surface: Size,
  seq: number,
): { windows: WindowState[]; seq: number } {
  const tiling = src.mode !== 'floating';
  const windows: WindowState[] = [];
  const add = (
    tabs: PaneState[],
    activeTab: number,
    rect: WindowRect,
    shown: boolean,
    headerCollapsed = false,
  ): void => {
    windows.push({
      id: windowId(seq),
      area: {
        kind: 'area',
        id: areaId(seq + 1),
        tabs,
        activeTab,
        ...(headerCollapsed ? { headerCollapsed: true } : {}),
      },
      rect: shown ? rect : hiddenRect(windows.length, surface),
      mode: shown ? 'normal' : 'minimized',
      z: windows.length + 1,
    });
    seq += 2;
  };

  // Everything is read before anything is placed: the grid's rects depend on which
  // docks were showing, but readPane dedupes instances first-come, so the centre is
  // still read first.
  const areas = collect(src.center, { x: 0, y: 0, w: 1, h: 1 }, readPane);
  const docks = (src.docks ?? {}) as Raw;
  const insets: Insets = { left: 0, right: 0, bottom: 0 };
  const sides = DOCK_SIDES.map((side) => {
    const dock = docks[side] as Raw | undefined;
    const rawTools = dock && Array.isArray(dock.tools) ? (dock.tools as unknown[]) : [];
    const size =
      typeof dock?.size === 'number' && dock.size >= 48 ? dock.size : DOCK_DEFAULTS[side];
    const tools = rawTools.flatMap((rawTool) => {
      const tool = readPane(rawTool);
      if (!tool) return [];
      const own = (rawTool as Raw).dockSize;
      const fraction = dockFraction(
        side,
        typeof own === 'number' && own >= 48 ? own : size,
        surface,
      );
      const showing = tiling && dock?.visible === true && dock.activeTool === tool.instanceId;
      if (showing) insets[side] = fraction;
      return [{ tool, fraction, showing }];
    });
    return { side, tools };
  });

  for (const area of areas) {
    // An area whose panes the user had all minimized stays put away.
    const shown = tiling && !area.minimized;
    const rect = onSurface(inGrid(area.unit, insets), surface);
    add(area.tabs, area.activeTab, rect, shown, area.headerCollapsed);
  }

  for (const { side, tools } of sides) {
    for (const { tool, fraction, showing } of tools) {
      add([tool], 0, dockRect(side, fraction, insets, surface), showing);
    }
  }

  return { windows, seq };
}
