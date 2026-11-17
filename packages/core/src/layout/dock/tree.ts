/**
 * Pure operations on the centre split tree.
 *
 * Revived from the tiling frame's `model.ts` (removed in 9738e4f5), which had
 * these right and tested. Every function is immutable and deterministic: ids come
 * from the caller's monotonic `seq` (`DockFrame.paneSeq`), so a caller holding a
 * snapshot can precompute what an operation will assign. Geometry works on the
 * unit square; `geometry.ts` turns it into pixels.
 */
import { areaId, createArea } from '../model';
import type { AreaNode, NavDirection, PaneState } from '../types';
import type { LayoutNode, Rect, SplitNode, SplitSide } from './types';

/** Alignment tolerance for rect comparisons on the unit square. */
const EPS = 1e-6;
/** No split child may shrink below this fraction of its split. */
export const MIN_FRACTION = 0.05;

export function splitId(seq: number): string {
  return `s${seq}`;
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

function renormalize(sizes: number[]): number[] {
  const total = sizes.reduce((a, b) => a + b, 0);
  if (total <= 0) return sizes.map(() => 1 / sizes.length);
  return sizes.map((s) => s / total);
}

/**
 * Canonical form: no single-child splits, no nested same-orientation splits
 * (children inlined with scaled fractions), sizes summing to 1.
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
 * Replace the node with `targetId` by whatever `replacer` returns (`null` removes
 * it). Removal hands the removed child's fraction to its siblings proportionally;
 * the result is normalized. Returns null only when the whole tree was removed.
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
  if (children.length === 0) return null;
  return normalize({ ...root, children, sizes: renormalize(sizes) });
}

/**
 * Split `targetAreaId` toward `side`, creating a new empty area beside it that
 * takes half its slot. When the parent split already runs that way the new area
 * becomes a sibling; otherwise the target is wrapped in a fresh split. Allocates
 * `areaId(seq)` (and `splitId(seq + 1)` when wrapping); returns the advanced seq.
 */
export function splitArea(
  root: LayoutNode,
  targetAreaId: string,
  side: SplitSide,
  seq: number,
): { root: LayoutNode; newAreaId: string; seq: number } | null {
  if (!findArea(root, targetAreaId)) return null;
  const orientation: SplitNode['orientation'] =
    side === 'left' || side === 'right' ? 'row' : 'column';
  const before = side === 'left' || side === 'above';
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

  const next = replaceNode(root, targetAreaId, (node) => ({
    kind: 'split',
    id: splitId(seq + 1),
    orientation,
    children: before ? [newArea, node] : [node, newArea],
    sizes: [0.5, 0.5],
  }));
  return next ? { root: next, newAreaId: newArea.id, seq: seq + 2 } : null;
}

/**
 * Remove an area from the tree. Removing the last one yields a fresh empty area:
 * the centre is never an empty tree.
 */
export function removeArea(
  root: LayoutNode,
  id: string,
  seq: number,
): { root: LayoutNode; seq: number } {
  const next = replaceNode(root, id, () => null);
  if (!next) return { root: createArea(areaId(seq)), seq: seq + 1 };
  return { root: next, seq };
}

// ---------------------------------------------------------------------------
// Geometry (unit square)
// ---------------------------------------------------------------------------

/** Bounds of every area and split within `within`, keyed by node id. */
export function computeRects(
  root: LayoutNode,
  within: Rect = { x: 0, y: 0, w: 1, h: 1 },
): Map<string, Rect> {
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
  walk(root, within);
  return rects;
}

/**
 * The area adjacent to `id` toward `direction`: it shares that edge, and of those
 * that do, it overlaps the most. Powers focus-move and move-pane-by-direction.
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

// ---------------------------------------------------------------------------
// Pane edits
// ---------------------------------------------------------------------------

/**
 * Remove a pane instance from the tree. An emptied area goes with it unless it is
 * the only one left.
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
  const next = replaceNode(root, area.id, (node) => withoutTab(node as AreaNode, instanceId));
  return next ? { root: next, removed, seq } : null;
}

/**
 * An area minus one tab. The active tab is carried by identity: closing a tab to
 * the left of the active one must not switch which pane is showing.
 */
export function withoutTab(area: AreaNode, instanceId: string): AreaNode {
  const active = area.tabs[area.activeTab];
  const tabs = area.tabs.filter((t) => t.instanceId !== instanceId);
  const kept = active && active.instanceId !== instanceId ? tabs.indexOf(active) : -1;
  return {
    ...area,
    tabs,
    activeTab: kept >= 0 ? kept : Math.min(area.activeTab, Math.max(tabs.length - 1, 0)),
  };
}

/** An area plus one tab, at `index` or the end. Activates it unless told not to. */
export function withTab(
  area: AreaNode,
  pane: PaneState,
  opts: { index?: number; activate?: boolean } = {},
): AreaNode {
  const at = Math.min(Math.max(opts.index ?? area.tabs.length, 0), area.tabs.length);
  const active = area.tabs[area.activeTab];
  const tabs = [...area.tabs];
  tabs.splice(at, 0, pane);
  const activeTab = opts.activate === false && active ? tabs.indexOf(active) : at;
  return { ...area, tabs, activeTab };
}
