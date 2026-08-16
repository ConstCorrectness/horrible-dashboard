/**
 * Pure operations on the frame layout tree. Every function is immutable (returns
 * new nodes, never mutates) and deterministic — id allocation draws from the
 * caller-supplied monotonic `seq` (`FrameState.paneSeq`), so a caller holding a
 * snapshot can precompute the ids an operation will assign. No React, no
 * registry, no DOM: geometry works on unit-square rects derived from the tree.
 */
import { DEFAULT_BACKDROP } from './types';
import type {
  AreaNode,
  AreaSplitDirection,
  DockSide,
  DockState,
  FrameState,
  LayoutNode,
  LocatedPane,
  NavDirection,
  PaneState,
  Rect,
  SplitNode,
  WindowState,
} from './types';

/** Alignment tolerance for rect comparisons on the unit square. */
const EPS = 1e-6;
/** No split child may shrink below this fraction of its split. */
export const MIN_FRACTION = 0.05;

export const DEFAULT_DOCK_SIZES: Record<DockSide, number> = {
  left: 280,
  right: 320,
  bottom: 240,
};

export function areaId(seq: number): string {
  return `a${seq}`;
}

export function splitId(seq: number): string {
  return `s${seq}`;
}

export function windowId(seq: number): string {
  return `w${seq}`;
}

export function instanceId(viewId: string, seq: number): string {
  return `${viewId}#${seq}`;
}

export function createArea(id: string): AreaNode {
  return { kind: 'area', id, tabs: [], activeTab: 0 };
}

export function createDock(side: DockSide): DockState {
  return { visible: false, size: DEFAULT_DOCK_SIZES[side], tools: [], activeTool: null };
}

/**
 * A minimal valid frame: one empty area, hidden docks, no windows.
 *
 * `mode` defaults to `tiling` rather than `floating` so that anything constructing a
 * frame without an explicit opinion — `resetLayout`, a preset seed, a blob too
 * corrupt to salvage — reproduces the pre-desktop behaviour. New desktops are made
 * floating by the code that creates them, deliberately, one place.
 */
export function createEmptyFrame(): FrameState {
  return {
    center: createArea(areaId(0)),
    docks: { left: createDock('left'), right: createDock('right'), bottom: createDock('bottom') },
    windows: [],
    windowViewport: null,
    mode: 'tiling',
    backdrop: { id: DEFAULT_BACKDROP },
    fullscreenAreaId: null,
    presentedInstanceId: null,
    focusedAreaId: areaId(0),
    focusedInstanceId: null,
    focusedWindowId: null,
    paneSeq: 1,
  };
}

// ---------------------------------------------------------------------------
// Walkers
// ---------------------------------------------------------------------------

export function collectAreas(node: LayoutNode, out: AreaNode[] = []): AreaNode[] {
  if (node.kind === 'area') out.push(node);
  else for (const child of node.children) collectAreas(child, out);
  return out;
}

export function findArea(node: LayoutNode, id: string): AreaNode | null {
  if (node.kind === 'area') return node.id === id ? node : null;
  for (const child of node.children) {
    const hit = findArea(child, id);
    if (hit) return hit;
  }
  return null;
}

/** The split whose `children` contains the node with `childId`, or null. */
export function findParentSplit(root: LayoutNode, childId: string): SplitNode | null {
  if (root.kind === 'area') return null;
  for (const child of root.children) {
    if (child.id === childId) return root;
    const hit = findParentSplit(child, childId);
    if (hit) return hit;
  }
  return null;
}

/** The area whose tabs contain `instanceId`, or null. */
export function areaOfInstance(node: LayoutNode, instanceId: string): AreaNode | null {
  if (node.kind === 'area') {
    return node.tabs.some((t) => t.instanceId === instanceId) ? node : null;
  }
  for (const child of node.children) {
    const hit = areaOfInstance(child, instanceId);
    if (hit) return hit;
  }
  return null;
}

export function firstArea(node: LayoutNode): AreaNode {
  return node.kind === 'area' ? node : firstArea(node.children[0]);
}

// ---------------------------------------------------------------------------
// Structural editing
// ---------------------------------------------------------------------------

/**
 * Replace the node with `targetId` by whatever `replacer` returns (`null`
 * removes it). Removal redistributes the removed child's fraction across its
 * siblings proportionally; the result is `normalize`d. Returns null only when
 * the target was the root and was removed.
 */
export function replaceNode(
  root: LayoutNode,
  targetId: string,
  replacer: (node: LayoutNode) => LayoutNode | null,
): LayoutNode | null {
  if (root.id === targetId) {
    const next = replacer(root);
    return next ? normalize(next) : null;
  }
  if (root.kind === 'area') return root;

  const children: LayoutNode[] = [];
  const sizes: number[] = [];
  let touched = false;
  for (let i = 0; i < root.children.length; i++) {
    const child = root.children[i];
    const next = replaceNode(child, targetId, replacer);
    if (next !== child) touched = true;
    if (next) {
      children.push(next);
      sizes.push(root.sizes[i]);
    }
  }
  if (!touched) return root;
  if (children.length === 0) return null as unknown as LayoutNode; // caller guards
  return normalize({ ...root, children, sizes: renormalize(sizes) });
}

function renormalize(sizes: number[]): number[] {
  const total = sizes.reduce((a, b) => a + b, 0);
  if (total <= 0) return sizes.map(() => 1 / sizes.length);
  return sizes.map((s) => s / total);
}

/**
 * Canonical form: no single-child splits, no nested same-orientation splits
 * (children inlined with scaled fractions), sizes summing to 1. Does NOT drop
 * empty areas — an empty area is a valid view-picker leaf.
 */
export function normalize(node: LayoutNode): LayoutNode {
  if (node.kind === 'area') return node;
  const flatChildren: LayoutNode[] = [];
  const flatSizes: number[] = [];
  for (let i = 0; i < node.children.length; i++) {
    const child = normalize(node.children[i]);
    const size = node.sizes[i] ?? 1 / node.children.length;
    if (child.kind === 'split' && child.orientation === node.orientation) {
      for (let j = 0; j < child.children.length; j++) {
        flatChildren.push(child.children[j]);
        flatSizes.push(size * child.sizes[j]);
      }
    } else {
      flatChildren.push(child);
      flatSizes.push(size);
    }
  }
  if (flatChildren.length === 1) return flatChildren[0];
  return { ...node, children: flatChildren, sizes: renormalize(flatSizes) };
}

/**
 * Split `areaId` toward `direction`, creating a new empty area beside it. The
 * new area takes half the target's slot. When the parent split already runs the
 * same orientation the new area is inserted as a sibling; otherwise the target
 * is wrapped in a fresh split. Allocates ids `areaId(seq)` (and `splitId(seq+1)`
 * when wrapping); returns the advanced seq.
 */
export function splitArea(
  root: LayoutNode,
  targetAreaId: string,
  direction: AreaSplitDirection,
  seq: number,
): { root: LayoutNode; newAreaId: string; seq: number } | null {
  if (!findArea(root, targetAreaId)) return null;
  const orientation: SplitNode['orientation'] =
    direction === 'left' || direction === 'right' ? 'row' : 'column';
  const before = direction === 'left' || direction === 'above';
  const newArea = createArea(areaId(seq));

  const parent = findParentSplit(root, targetAreaId);
  if (parent && parent.orientation === orientation) {
    const next = replaceNode(root, parent.id, (node) => {
      const split = node as SplitNode;
      const idx = split.children.findIndex((c) => c.id === targetAreaId);
      const children = [...split.children];
      const sizes = [...split.sizes];
      const half = sizes[idx] / 2;
      sizes[idx] = half;
      children.splice(before ? idx : idx + 1, 0, newArea);
      sizes.splice(before ? idx : idx + 1, 0, half);
      return { ...split, children, sizes };
    });
    return next ? { root: next, newAreaId: newArea.id, seq: seq + 1 } : null;
  }

  const wrapper: SplitNode = {
    kind: 'split',
    id: splitId(seq + 1),
    orientation,
    children: [],
    sizes: [0.5, 0.5],
  };
  const next = replaceNode(root, targetAreaId, (node) => ({
    ...wrapper,
    children: before ? [newArea, node] : [node, newArea],
  }));
  return next ? { root: next, newAreaId: newArea.id, seq: seq + 2 } : null;
}

/**
 * Remove an area from the tree. Removing the last area yields a fresh empty
 * area (the grid is never empty). Sibling fractions renormalize.
 */
export function removeArea(
  root: LayoutNode,
  id: string,
  seq: number,
): {
  root: LayoutNode;
  seq: number;
} {
  const next = replaceNode(root, id, () => null);
  if (!next) return { root: createArea(areaId(seq)), seq: seq + 1 };
  return { root: next, seq };
}

// ---------------------------------------------------------------------------
// Geometry (unit square)
// ---------------------------------------------------------------------------

/** Bounds of every area (and split) on the unit square, keyed by node id. */
export function computeRects(root: LayoutNode): Map<string, Rect> {
  const rects = new Map<string, Rect>();
  const walk = (node: LayoutNode, rect: Rect): void => {
    rects.set(node.id, rect);
    if (node.kind === 'area') return;
    let offset = node.orientation === 'row' ? rect.x : rect.y;
    for (let i = 0; i < node.children.length; i++) {
      const frac = node.sizes[i];
      const child = node.children[i];
      if (node.orientation === 'row') {
        const w = rect.w * frac;
        walk(child, { x: offset, y: rect.y, w, h: rect.h });
        offset += w;
      } else {
        const h = rect.h * frac;
        walk(child, { x: rect.x, y: offset, w: rect.w, h });
        offset += h;
      }
    }
  };
  walk(root, { x: 0, y: 0, w: 1, h: 1 });
  return rects;
}

/**
 * The area adjacent to `id` toward `direction` — shares that edge, maximal
 * cross-axis overlap. Powers focus-move, move-pane-by-direction, and join.
 */
export function neighborAreaId(
  root: LayoutNode,
  id: string,
  direction: NavDirection,
): string | null {
  const rects = computeRects(root);
  const rect = rects.get(id);
  if (!rect) return null;
  let best: { id: string; overlap: number } | null = null;
  for (const area of collectAreas(root)) {
    if (area.id === id) continue;
    const cand = rects.get(area.id);
    if (!cand) continue;
    const touches =
      direction === 'left'
        ? Math.abs(cand.x + cand.w - rect.x) < EPS
        : direction === 'right'
          ? Math.abs(rect.x + rect.w - cand.x) < EPS
          : direction === 'up'
            ? Math.abs(cand.y + cand.h - rect.y) < EPS
            : Math.abs(rect.y + rect.h - cand.y) < EPS;
    if (!touches) continue;
    const overlap =
      direction === 'left' || direction === 'right'
        ? Math.min(rect.y + rect.h, cand.y + cand.h) - Math.max(rect.y, cand.y)
        : Math.min(rect.x + rect.w, cand.x + cand.w) - Math.max(rect.x, cand.x);
    if (overlap <= EPS) continue;
    if (!best || overlap > best.overlap) best = { id: area.id, overlap };
  }
  return best?.id ?? null;
}

/**
 * Join the neighbor toward `direction` into `id` (Blender-style: the neighbor
 * area disappears, `id` absorbs its space). Only legal when the two are
 * adjacent siblings whose shared edge spans both fully — which, post-normalize,
 * is exactly the aligned case. Returns the removed area so the caller can adopt
 * its tabs (role permitting).
 */
export function joinArea(
  root: LayoutNode,
  id: string,
  direction: NavDirection,
): { root: LayoutNode; removed: AreaNode } | null {
  const neighborId = neighborAreaId(root, id, direction);
  if (!neighborId) return null;
  const parent = findParentSplit(root, id);
  if (!parent || !parent.children.some((c) => c.id === neighborId)) return null;
  const axis = direction === 'left' || direction === 'right' ? 'row' : 'column';
  if (parent.orientation !== axis) return null;

  const rects = computeRects(root);
  const a = rects.get(id)!;
  const b = rects.get(neighborId)!;
  const aligned =
    axis === 'row'
      ? Math.abs(a.y - b.y) < EPS && Math.abs(a.h - b.h) < EPS
      : Math.abs(a.x - b.x) < EPS && Math.abs(a.w - b.w) < EPS;
  if (!aligned) return null;

  const removed = findArea(root, neighborId)!;
  const next = replaceNode(root, parent.id, (node) => {
    const split = node as SplitNode;
    const keepIdx = split.children.findIndex((c) => c.id === id);
    const dropIdx = split.children.findIndex((c) => c.id === neighborId);
    const children = split.children.filter((_, i) => i !== dropIdx);
    const sizes = [...split.sizes];
    sizes[keepIdx] += sizes[dropIdx];
    sizes.splice(dropIdx, 1);
    return { ...split, children, sizes };
  });
  return next ? { root: next, removed } : null;
}

/** Overwrite a split's child fractions (clamped to MIN_FRACTION, renormalized). */
export function setSplitSizes(root: LayoutNode, id: string, sizes: number[]): LayoutNode | null {
  let found = false;
  const next = replaceNode(root, id, (node) => {
    if (node.kind !== 'split' || sizes.length !== node.children.length) return node;
    found = true;
    return { ...node, sizes: renormalize(sizes.map((s) => Math.max(s, MIN_FRACTION))) };
  });
  return found && next ? next : null;
}

/**
 * Resize an area toward target unit-square fractions of the whole center
 * (`w` and/or `h` in 0..1). Adjusts the area's slot in the nearest matching-
 * orientation ancestor split; the delta comes out of the adjacent sibling.
 */
export function resizeArea(
  root: LayoutNode,
  id: string,
  target: { w?: number; h?: number },
): LayoutNode | null {
  let current = root;
  let changed = false;
  for (const [axis, want] of [
    ['row', target.w],
    ['column', target.h],
  ] as const) {
    if (want === undefined) continue;
    const split = nearestSplit(current, id, axis);
    if (!split) continue;
    const rects = computeRects(current);
    const splitRect = rects.get(split.id)!;
    const extent = axis === 'row' ? splitRect.w : splitRect.h;
    if (extent <= 0) continue;
    const childId = childContaining(split, id);
    const idx = split.children.findIndex((c) => c.id === childId);
    const desired = Math.min(Math.max(want / extent, MIN_FRACTION), 1 - MIN_FRACTION);
    const delta = desired - split.sizes[idx];
    const neighborIdx = idx + 1 < split.children.length ? idx + 1 : idx - 1;
    if (neighborIdx < 0) continue;
    const sizes = [...split.sizes];
    if (sizes[neighborIdx] - delta < MIN_FRACTION) continue;
    sizes[idx] = desired;
    sizes[neighborIdx] -= delta;
    const next = setSplitSizes(current, split.id, sizes);
    if (next) {
      current = next;
      changed = true;
    }
  }
  return changed ? current : null;
}

/** Nearest ancestor split of `id` running `orientation` (may contain it deeply). */
function nearestSplit(
  root: LayoutNode,
  id: string,
  orientation: SplitNode['orientation'],
): SplitNode | null {
  let childId = id;
  for (;;) {
    const parent = findParentSplit(root, childId);
    if (!parent) return null;
    if (parent.orientation === orientation) return parent;
    childId = parent.id;
  }
}

/** The direct child of `split` whose subtree contains node `id`. */
function childContaining(split: SplitNode, id: string): string {
  for (const child of split.children) {
    if (child.id === id) return child.id;
    if (child.kind === 'split' && subtreeHas(child, id)) return child.id;
  }
  return id;
}

function subtreeHas(node: LayoutNode, id: string): boolean {
  if (node.id === id) return true;
  return node.kind === 'split' && node.children.some((c) => subtreeHas(c, id));
}

// ---------------------------------------------------------------------------
// Pane edits (center tree)
// ---------------------------------------------------------------------------

export function insertPane(
  root: LayoutNode,
  targetAreaId: string,
  pane: PaneState,
  opts: { activate?: boolean } = {},
): LayoutNode | null {
  let found = false;
  const next = replaceNode(root, targetAreaId, (node) => {
    const area = node as AreaNode;
    found = true;
    const tabs = [...area.tabs, pane];
    return {
      ...area,
      tabs,
      activeTab: opts.activate === false ? area.activeTab : tabs.length - 1,
    };
  });
  return found && next ? next : null;
}

/**
 * Remove a pane instance from the center tree. An emptied area is removed with
 * it unless it is the only area left. Returns the removed pane for reuse
 * (moves, floats).
 */
export function removePane(
  root: LayoutNode,
  instanceId: string,
  seq: number,
): { root: LayoutNode; removed: PaneState; seq: number } | null {
  const area = areaOfInstance(root, instanceId);
  if (!area) return null;
  const removed = area.tabs.find((t) => t.instanceId === instanceId)!;
  const remaining = area.tabs.filter((t) => t.instanceId !== instanceId);
  if (remaining.length === 0 && collectAreas(root).length > 1) {
    const dropped = removeArea(root, area.id, seq);
    return { root: dropped.root, removed, seq: dropped.seq };
  }
  const next = replaceNode(root, area.id, (node) => ({
    ...(node as AreaNode),
    tabs: remaining,
    activeTab: Math.min((node as AreaNode).activeTab, Math.max(remaining.length - 1, 0)),
  }));
  return next ? { root: next, removed, seq } : null;
}

export function setActiveTab(root: LayoutNode, areaId: string, index: number): LayoutNode | null {
  let found = false;
  const next = replaceNode(root, areaId, (node) => {
    const area = node as AreaNode;
    if (index < 0 || index >= area.tabs.length) return area;
    found = true;
    return { ...area, activeTab: index };
  });
  return found && next ? next : null;
}

/**
 * Move one tab within its area, splice-style: the tab at `from` is pulled out and
 * re-inserted so it ends up at `to` in the *resulting* list.
 *
 * `activeTab` is carried by identity, not by index — it is a position, and every
 * naive reorder bug is the same one: the user drags a background tab past the
 * active one and the area silently switches to a different pane. Which pane is on
 * screen must not change because the strip was rearranged.
 */
export function reorderTab(
  root: LayoutNode,
  areaId: string,
  from: number,
  to: number,
): LayoutNode | null {
  let found = false;
  const next = replaceNode(root, areaId, (node) => {
    const area = node as AreaNode;
    const n = area.tabs.length;
    if (from < 0 || from >= n || to < 0 || to >= n || from === to) return area;
    found = true;
    const active = area.tabs[area.activeTab];
    const tabs = [...area.tabs];
    const [moved] = tabs.splice(from, 1);
    tabs.splice(to, 0, moved);
    const activeTab = active ? tabs.indexOf(active) : area.activeTab;
    return { ...area, tabs, activeTab: activeTab >= 0 ? activeTab : 0 };
  });
  return found && next ? next : null;
}

// ---------------------------------------------------------------------------
// Frame-level pane lookups/edits (center + docks + windows)
// ---------------------------------------------------------------------------

export function findWindow(frame: FrameState, id: string): WindowState | null {
  return frame.windows.find((w) => w.id === id) ?? null;
}

/** The window whose area holds `instanceId`, or null. */
export function windowOfInstance(frame: FrameState, instanceId: string): WindowState | null {
  return frame.windows.find((w) => w.area.tabs.some((t) => t.instanceId === instanceId)) ?? null;
}

/** Where an area lives — the center tree, or one window. */
export type AreaHost = { kind: 'center' } | { kind: 'window'; windowId: string };

/**
 * Find an area by id **anywhere**: the center tree or any window.
 *
 * This is what lets a window's tabs be driven by the same verbs a center area's are
 * (`SET_ACTIVE_TAB`, `REORDER_TAB`, `INSERT_PANE`, `dropPaneOnTab`) instead of a
 * parallel set that would drift. Center is searched first — window area ids come from
 * the same `paneSeq` counter, so they never collide.
 */
export function findAreaAnywhere(
  frame: FrameState,
  id: string,
): { area: AreaNode; host: AreaHost } | null {
  const inCenter = findArea(frame.center, id);
  if (inCenter) return { area: inCenter, host: { kind: 'center' } };
  const win = frame.windows.find((w) => w.area.id === id);
  return win ? { area: win.area, host: { kind: 'window', windowId: win.id } } : null;
}

/** Apply `fn` to an area wherever it lives. Null if unknown. */
export function updateAreaAnywhere(
  frame: FrameState,
  id: string,
  fn: (area: AreaNode) => AreaNode,
): FrameState | null {
  const found = findAreaAnywhere(frame, id);
  if (!found) return null;
  if (found.host.kind === 'window') {
    const windowId = found.host.windowId;
    return {
      ...frame,
      windows: frame.windows.map((w) => (w.id === windowId ? { ...w, area: fn(w.area) } : w)),
    };
  }
  const center = replaceNode(frame.center, id, (node) => fn(node as AreaNode));
  return center ? { ...frame, center } : null;
}

export function findPaneAnywhere(frame: FrameState, instanceId: string): LocatedPane | null {
  const area = areaOfInstance(frame.center, instanceId);
  if (area) {
    return {
      pane: area.tabs.find((t) => t.instanceId === instanceId)!,
      location: { kind: 'area', areaId: area.id },
    };
  }
  for (const side of ['left', 'right', 'bottom'] as const) {
    const tool = frame.docks[side].tools.find((t) => t.instanceId === instanceId);
    if (tool) return { pane: tool, location: { kind: 'dock', dock: side } };
  }
  const win = windowOfInstance(frame, instanceId);
  if (win) {
    return {
      pane: win.area.tabs.find((t) => t.instanceId === instanceId)!,
      location: { kind: 'window', windowId: win.id, areaId: win.area.id },
    };
  }
  return null;
}

export function listPanes(frame: FrameState): LocatedPane[] {
  const out: LocatedPane[] = [];
  for (const area of collectAreas(frame.center)) {
    for (const tab of area.tabs) {
      out.push({ pane: tab, location: { kind: 'area', areaId: area.id } });
    }
  }
  for (const side of ['left', 'right', 'bottom'] as const) {
    for (const tool of frame.docks[side].tools) {
      out.push({ pane: tool, location: { kind: 'dock', dock: side } });
    }
  }
  for (const win of frame.windows) {
    for (const tab of win.area.tabs) {
      out.push({ pane: tab, location: { kind: 'window', windowId: win.id, areaId: win.area.id } });
    }
  }
  return out;
}

/**
 * The panes actually on screen: each area's active tab, each visible dock's active
 * tool, and each non-minimized window's active tab. Distinct from `listPanes`, which
 * includes the background tabs — those are unmounted, so their live state isn't
 * readable anyway. A fullscreened area hides everything else, including the docks.
 *
 * A **minimized** window is excluded even though its pane stays mounted: "visible"
 * here means "the user can see it", which is what the agent's context builder and
 * the screenshot paths ask this for.
 */
export function visiblePanes(frame: FrameState): LocatedPane[] {
  const out: LocatedPane[] = [];
  const areas = collectAreas(frame.center);
  const shown = frame.fullscreenAreaId
    ? areas.filter((a) => a.id === frame.fullscreenAreaId)
    : areas;
  for (const area of shown) {
    const tab = area.tabs[area.activeTab];
    if (tab) out.push({ pane: tab, location: { kind: 'area', areaId: area.id } });
  }
  if (frame.fullscreenAreaId) return out;
  for (const side of ['left', 'right', 'bottom'] as const) {
    const dock = frame.docks[side];
    if (!dock.visible) continue;
    const tool = dock.tools.find((t) => t.instanceId === dock.activeTool);
    if (tool) out.push({ pane: tool, location: { kind: 'dock', dock: side } });
  }
  for (const win of frame.windows) {
    if (win.mode === 'minimized') continue;
    const tab = win.area.tabs[win.area.activeTab];
    if (tab)
      out.push({ pane: tab, location: { kind: 'window', windowId: win.id, areaId: win.area.id } });
  }
  return out;
}

/** Apply `fn` to a pane instance wherever it lives. Null if unknown. */
export function updatePaneAnywhere(
  frame: FrameState,
  instanceId: string,
  fn: (pane: PaneState) => PaneState,
): FrameState | null {
  const located = findPaneAnywhere(frame, instanceId);
  if (!located) return null;
  const { location } = located;
  if (location.kind === 'area') {
    const center = replaceNode(frame.center, location.areaId, (node) => {
      const area = node as AreaNode;
      return {
        ...area,
        tabs: area.tabs.map((t) => (t.instanceId === instanceId ? fn(t) : t)),
      };
    });
    return center ? { ...frame, center } : null;
  }
  if (location.kind === 'dock') {
    const dock = frame.docks[location.dock];
    return {
      ...frame,
      docks: {
        ...frame.docks,
        [location.dock]: {
          ...dock,
          tools: dock.tools.map((t) => (t.instanceId === instanceId ? fn(t) : t)),
        },
      },
    };
  }
  return {
    ...frame,
    windows: frame.windows.map((w) =>
      w.id === location.windowId
        ? {
            ...w,
            area: {
              ...w.area,
              tabs: w.area.tabs.map((t) => (t.instanceId === instanceId ? fn(t) : t)),
            },
          }
        : w,
    ),
  };
}

/** Remove a pane instance from wherever it lives (center, dock, or window). */
export function removePaneAnywhere(
  frame: FrameState,
  instanceId: string,
): { frame: FrameState; removed: PaneState } | null {
  const located = findPaneAnywhere(frame, instanceId);
  if (!located) return null;
  const { location } = located;
  if (location.kind === 'area') {
    const res = removePane(frame.center, instanceId, frame.paneSeq);
    if (!res) return null;
    const focusGone = !findArea(res.root, frame.focusedAreaId ?? '');
    const fullscreenGone = !findArea(res.root, frame.fullscreenAreaId ?? '');
    return {
      frame: {
        ...frame,
        center: res.root,
        paneSeq: res.seq,
        focusedAreaId: focusGone ? firstArea(res.root).id : frame.focusedAreaId,
        fullscreenAreaId: fullscreenGone ? null : frame.fullscreenAreaId,
      },
      removed: res.removed,
    };
  }
  if (location.kind === 'dock') {
    const dock = frame.docks[location.dock];
    const removed = dock.tools.find((t) => t.instanceId === instanceId)!;
    const tools = dock.tools.filter((t) => t.instanceId !== instanceId);
    return {
      frame: {
        ...frame,
        docks: {
          ...frame.docks,
          [location.dock]: {
            ...dock,
            tools,
            activeTool:
              dock.activeTool === instanceId ? (tools[0]?.instanceId ?? null) : dock.activeTool,
            visible: tools.length === 0 ? false : dock.visible,
          },
        },
      },
      removed,
    };
  }
  const win = frame.windows.find((w) => w.id === location.windowId)!;
  const removed = win.area.tabs.find((t) => t.instanceId === instanceId)!;
  const tabs = win.area.tabs.filter((t) => t.instanceId !== instanceId);
  const windows = tabs.length
    ? frame.windows.map((w) =>
        w === win
          ? {
              ...w,
              area: { ...w.area, tabs, activeTab: Math.min(w.area.activeTab, tabs.length - 1) },
            }
          : w,
      )
    : // A window is its area: emptying the last tab closes the window rather than
      // leaving a titlebar with nothing under it.
      frame.windows.filter((w) => w !== win);
  return {
    frame: {
      ...frame,
      windows,
      focusedWindowId:
        tabs.length === 0 && frame.focusedWindowId === win.id ? null : frame.focusedWindowId,
    },
    removed,
  };
}
