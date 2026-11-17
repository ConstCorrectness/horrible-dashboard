/**
 * The centre split tree: the invariants every other dock module leans on.
 */
import { describe, expect, it } from 'vitest';

import { createArea } from '../../model';
import type { AreaNode, PaneState } from '../../types';
import {
  collectAreas,
  computeRects,
  findParentSplit,
  neighborAreaId,
  normalize,
  removePane,
  setSplitSizes,
  splitArea,
  withTab,
  withoutTab,
} from '../tree';
import type { LayoutNode, SplitNode } from '../types';

const pane = (id: string): PaneState => ({ instanceId: id, viewId: id.split('#')[0] });
const area = (id: string, ...tabs: string[]): AreaNode => ({
  ...createArea(id),
  tabs: tabs.map(pane),
});
const row = (id: string, ...children: LayoutNode[]): SplitNode => ({
  kind: 'split',
  id,
  orientation: 'row',
  children,
  sizes: children.map(() => 1 / children.length),
});
const column = (id: string, ...children: LayoutNode[]): SplitNode => ({
  ...row(id, ...children),
  orientation: 'column',
});

const total = (node: LayoutNode): number =>
  node.kind === 'split' ? node.sizes.reduce((a, b) => a + b, 0) : 1;

describe('splitArea', () => {
  it('wraps a lone area in a split and gives the new area half', () => {
    const res = splitArea(area('a0', 'x#1'), 'a0', 'right', 5)!;
    const root = res.root as SplitNode;
    expect(root.orientation).toBe('row');
    expect(root.children.map((c) => c.id)).toEqual(['a0', 'a5']);
    expect(root.sizes).toEqual([0.5, 0.5]);
    expect(res.newAreaId).toBe('a5');
    // One id for the area, one for the wrapping split.
    expect(res.seq).toBe(7);
  });

  it('puts the new area before the target for left and above', () => {
    const left = splitArea(area('a0'), 'a0', 'left', 5)!.root as SplitNode;
    expect(left.children.map((c) => c.id)).toEqual(['a5', 'a0']);
    const above = splitArea(area('a0'), 'a0', 'above', 5)!.root as SplitNode;
    expect(above.orientation).toBe('column');
    expect(above.children.map((c) => c.id)).toEqual(['a5', 'a0']);
  });

  it('adds a sibling instead of nesting when the parent already runs that way', () => {
    const res = splitArea(row('s1', area('a0'), area('a1')), 'a1', 'right', 5)!;
    const root = res.root as SplitNode;
    expect(root.id).toBe('s1');
    expect(root.children.map((c) => c.id)).toEqual(['a0', 'a1', 'a5']);
    expect(root.sizes).toEqual([0.5, 0.25, 0.25]);
    // No wrapper, so only the area id was spent.
    expect(res.seq).toBe(6);
  });

  it('returns null for an unknown area', () => {
    expect(splitArea(area('a0'), 'nope', 'right', 5)).toBeNull();
  });
});

describe('normalize', () => {
  it('inlines a same-orientation child and keeps the fractions', () => {
    const tree = row('s1', area('a0'), row('s2', area('a1'), area('a2')));
    const flat = normalize(tree) as SplitNode;
    expect(flat.children.map((c) => c.id)).toEqual(['a0', 'a1', 'a2']);
    expect(flat.sizes).toEqual([0.5, 0.25, 0.25]);
  });

  it('collapses a single-child split to the child', () => {
    expect(normalize({ ...row('s1', area('a0')), sizes: [1] }).id).toBe('a0');
  });
});

describe('removePane', () => {
  it('drops an emptied area and gives its space to the siblings', () => {
    const tree = row('s1', area('a0', 'x#1'), area('a1', 'y#2'), area('a2', 'z#3'));
    const res = removePane(tree, 'y#2', 9)!;
    const root = res.root as SplitNode;
    expect(root.children.map((c) => c.id)).toEqual(['a0', 'a2']);
    expect(total(root)).toBeCloseTo(1);
    expect(res.removed.instanceId).toBe('y#2');
  });

  it('collapses the split when one area is left', () => {
    const res = removePane(row('s1', area('a0', 'x#1'), area('a1', 'y#2')), 'y#2', 9)!;
    expect(res.root.id).toBe('a0');
  });

  it('keeps the last area, empty: the centre is never an empty tree', () => {
    const res = removePane(area('a0', 'x#1'), 'x#1', 9)!;
    expect(res.root).toMatchObject({ kind: 'area', id: 'a0', tabs: [] });
  });

  it('leaves no split with fewer than two children anywhere', () => {
    const tree = row('s1', area('a0', 'x#1'), column('s2', area('a1', 'y#2'), area('a2', 'z#3')));
    const res = removePane(tree, 'z#3', 9)!;
    const check = (node: LayoutNode): void => {
      if (node.kind === 'area') return;
      expect(node.children.length).toBeGreaterThanOrEqual(2);
      node.children.forEach(check);
    };
    check(res.root);
    expect(collectAreas(res.root).map((a) => a.id)).toEqual(['a0', 'a1']);
  });
});

describe('tab edits', () => {
  it('closing a tab left of the active one does not change which pane shows', () => {
    const a = { ...area('a0', 'x#1', 'y#2', 'z#3'), activeTab: 2 };
    const next = withoutTab(a, 'x#1');
    expect(next.tabs[next.activeTab].instanceId).toBe('z#3');
  });

  it('closing the active tab shows the one that took its place', () => {
    const a = { ...area('a0', 'x#1', 'y#2', 'z#3'), activeTab: 1 };
    const next = withoutTab(a, 'y#2');
    expect(next.tabs[next.activeTab].instanceId).toBe('z#3');
  });

  it('inserting without activating keeps the showing pane, wherever it lands', () => {
    const a = { ...area('a0', 'x#1', 'y#2'), activeTab: 1 };
    const next = withTab(a, pane('n#9'), { index: 0, activate: false });
    expect(next.tabs.map((t) => t.instanceId)).toEqual(['n#9', 'x#1', 'y#2']);
    expect(next.tabs[next.activeTab].instanceId).toBe('y#2');
  });
});

describe('geometry', () => {
  const tree = row('s1', area('a0'), column('s2', area('a1'), area('a2')));

  it('tiles the unit square exactly', () => {
    const rects = computeRects(tree);
    const cover = ['a0', 'a1', 'a2'].reduce((sum, id) => {
      const r = rects.get(id)!;
      return sum + r.w * r.h;
    }, 0);
    expect(cover).toBeCloseTo(1);
    expect(rects.get('a2')).toEqual({ x: 0.5, y: 0.5, w: 0.5, h: 0.5 });
  });

  it('lays out inside the box it is given', () => {
    const rects = computeRects(tree, { x: 100, y: 0, w: 800, h: 600 });
    expect(rects.get('a0')).toEqual({ x: 100, y: 0, w: 400, h: 600 });
    expect(rects.get('a2')).toEqual({ x: 500, y: 300, w: 400, h: 300 });
  });

  it('finds neighbours across a nested split', () => {
    expect(neighborAreaId(tree, 'a1', 'left')).toBe('a0');
    expect(neighborAreaId(tree, 'a1', 'down')).toBe('a2');
    expect(neighborAreaId(tree, 'a0', 'left')).toBeNull();
    // a0 touches both right-hand areas equally; the first wins, deterministically.
    expect(neighborAreaId(tree, 'a0', 'right')).toBe('a1');
  });
});

describe('setSplitSizes', () => {
  it('never lets a child shrink to nothing', () => {
    const next = setSplitSizes(row('s1', area('a0'), area('a1')), 's1', [1, 0])! as SplitNode;
    expect(next.sizes[1]).toBeGreaterThan(0.04);
    expect(total(next)).toBeCloseTo(1);
  });

  it('rejects a size list of the wrong length', () => {
    expect(setSplitSizes(row('s1', area('a0'), area('a1')), 's1', [1])).toBeNull();
  });

  it('finds a nested split by id', () => {
    const tree = row('s1', area('a0'), column('s2', area('a1'), area('a2')));
    const next = setSplitSizes(tree, 's2', [0.75, 0.25])!;
    expect(findParentSplit(next, 'a1')!.sizes).toEqual([0.75, 0.25]);
  });
});
