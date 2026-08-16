import { describe, expect, it } from 'vitest';

import {
  areaOfInstance,
  collectAreas,
  computeRects,
  createArea,
  createEmptyFrame,
  findArea,
  findPaneAnywhere,
  insertPane,
  joinArea,
  listPanes,
  neighborAreaId,
  normalize,
  removePane,
  removePaneAnywhere,
  reorderTab,
  resizeArea,
  setSplitSizes,
  splitArea,
  updatePaneAnywhere,
} from '../model';
import type { AreaNode, FrameState, LayoutNode, PaneState, SplitNode } from '../types';

function pane(viewId: string, n: number): PaneState {
  return { instanceId: `${viewId}#${n}`, viewId };
}

/** row[ a1 | a2 ] with one pane each. */
function twoAreas(): { root: LayoutNode; left: string; right: string } {
  let root: LayoutNode = createArea('a1');
  root = insertPane(root, 'a1', pane('scratch.note', 1))!;
  const split = splitArea(root, 'a1', 'right', 2)!;
  root = insertPane(split.root, split.newAreaId, pane('scratch.note', 3))!;
  return { root, left: 'a1', right: split.newAreaId };
}

describe('splitArea', () => {
  it('wraps an area in a new split and halves the slot', () => {
    const root = createArea('a0');
    const res = splitArea(root, 'a0', 'right', 1)!;
    expect(res.newAreaId).toBe('a1');
    const split = res.root as SplitNode;
    expect(split.kind).toBe('split');
    expect(split.orientation).toBe('row');
    expect(split.children.map((c) => c.id)).toEqual(['a0', 'a1']);
    expect(split.sizes).toEqual([0.5, 0.5]);
    expect(res.seq).toBe(3); // consumed area id + wrapper split id
  });

  it('inserts a sibling when the parent split runs the same orientation', () => {
    const { root } = twoAreas();
    const res = splitArea(root, 'a1', 'right', 10)!;
    const split = res.root as SplitNode;
    expect(split.children).toHaveLength(3);
    expect(split.children[1].id).toBe('a10'); // beside a1, before a2
    expect(split.sizes[0]).toBeCloseTo(0.25);
    expect(split.sizes[1]).toBeCloseTo(0.25);
    expect(split.sizes[2]).toBeCloseTo(0.5);
    expect(res.seq).toBe(11); // sibling insert consumes only the area id
  });

  it('splitting before places the new area first', () => {
    const root = createArea('a0');
    const res = splitArea(root, 'a0', 'above', 1)!;
    const split = res.root as SplitNode;
    expect(split.orientation).toBe('column');
    expect(split.children[0].id).toBe('a1');
  });

  it('returns null for an unknown area', () => {
    expect(splitArea(createArea('a0'), 'nope', 'right', 1)).toBeNull();
  });
});

describe('normalize', () => {
  it('flattens nested same-orientation splits with scaled sizes', () => {
    const nested: SplitNode = {
      kind: 'split',
      id: 's1',
      orientation: 'row',
      sizes: [0.5, 0.5],
      children: [
        createArea('a1'),
        {
          kind: 'split',
          id: 's2',
          orientation: 'row',
          sizes: [0.4, 0.6],
          children: [createArea('a2'), createArea('a3')],
        },
      ],
    };
    const flat = normalize(nested) as SplitNode;
    expect(flat.children.map((c) => c.id)).toEqual(['a1', 'a2', 'a3']);
    expect(flat.sizes[1]).toBeCloseTo(0.2);
    expect(flat.sizes[2]).toBeCloseTo(0.3);
  });

  it('collapses single-child splits', () => {
    const single: SplitNode = {
      kind: 'split',
      id: 's1',
      orientation: 'column',
      sizes: [1],
      children: [createArea('a1')],
    };
    expect(normalize(single).id).toBe('a1');
  });
});

describe('joinArea', () => {
  it('absorbs an aligned sibling and returns it for tab adoption', () => {
    const { root, left, right } = twoAreas();
    const res = joinArea(root, left, 'right')!;
    expect(res.removed.id).toBe(right);
    expect(res.removed.tabs).toHaveLength(1);
    expect(res.root.kind).toBe('area'); // split collapsed away
    expect(res.root.id).toBe(left);
  });

  it('refuses to join across a misaligned edge', () => {
    // row[ a1 | column[ a2 ; a3 ] ] — a1 spans full height, a2 only half.
    const { root, right } = twoAreas();
    const res = splitArea(root, right, 'below', 20)!;
    expect(joinArea(res.root, 'a1', 'right')).toBeNull();
    // But the two stacked halves are joinable with each other.
    expect(joinArea(res.root, right, 'down')).not.toBeNull();
  });
});

describe('neighborAreaId / computeRects', () => {
  it('finds directional neighbors on the unit square', () => {
    const { root, left, right } = twoAreas();
    expect(neighborAreaId(root, left, 'right')).toBe(right);
    expect(neighborAreaId(root, right, 'left')).toBe(left);
    expect(neighborAreaId(root, left, 'left')).toBeNull();
    expect(neighborAreaId(root, left, 'up')).toBeNull();
  });

  it('prefers the neighbor with the largest shared edge', () => {
    // row[ a1 | column[ a2 (0.7) ; a3 (0.3) ] ]
    const { root, right } = twoAreas();
    const res = splitArea(root, right, 'below', 20)!;
    const sized = setSplitSizes(
      res.root,
      (findParentSplitOf(res.root, right) as SplitNode).id,
      [0.7, 0.3],
    )!;
    expect(neighborAreaId(sized, 'a1', 'right')).toBe(right);
  });

  it('rects tile the unit square', () => {
    const { root } = twoAreas();
    const rects = computeRects(root);
    const total = collectAreas(root)
      .map((a) => rects.get(a.id)!)
      .reduce((sum, r) => sum + r.w * r.h, 0);
    expect(total).toBeCloseTo(1);
  });
});

function findParentSplitOf(root: LayoutNode, id: string): SplitNode | null {
  if (root.kind === 'area') return null;
  for (const child of root.children) {
    if (child.id === id) return root;
    const hit = findParentSplitOf(child, id);
    if (hit) return hit;
  }
  return null;
}

describe('resizeArea', () => {
  it('resizes toward a target fraction of the center', () => {
    const { root, left } = twoAreas();
    const resized = resizeArea(root, left, { w: 0.7 })!;
    const rects = computeRects(resized);
    expect(rects.get(left)!.w).toBeCloseTo(0.7);
  });

  it('returns null when no matching-orientation ancestor exists', () => {
    const { root, left } = twoAreas();
    expect(resizeArea(root, left, { h: 0.3 })).toBeNull();
  });
});

describe('removePane', () => {
  it('drops an emptied area and renormalizes the split', () => {
    const { root, right } = twoAreas();
    const area = findArea(root, right)!;
    const res = removePane(root, area.tabs[0].instanceId, 50)!;
    expect(res.root.kind).toBe('area');
    expect(res.root.id).toBe('a1');
  });

  it('keeps the sole area when it empties', () => {
    let root: LayoutNode = createArea('a1');
    root = insertPane(root, 'a1', pane('scratch.note', 1))!;
    const res = removePane(root, 'scratch.note#1', 5)!;
    expect((res.root as AreaNode).tabs).toHaveLength(0);
    expect(res.root.id).toBe('a1');
  });

  it('clamps the active tab', () => {
    let root: LayoutNode = createArea('a1');
    root = insertPane(root, 'a1', pane('scratch.note', 1))!;
    root = insertPane(root, 'a1', pane('scratch.note', 2))!;
    const res = removePane(root, 'scratch.note#2', 5)!;
    expect((res.root as AreaNode).activeTab).toBe(0);
  });
});

describe('reorderTab', () => {
  /** a1 holding three tabs, with `active` showing. */
  function threeTabs(active: number): LayoutNode {
    let root: LayoutNode = createArea('a1');
    for (const n of [1, 2, 3]) root = insertPane(root, 'a1', pane('scratch.note', n))!;
    return { ...(root as AreaNode), activeTab: active };
  }
  const ids = (root: LayoutNode) => (root as AreaNode).tabs.map((t) => t.instanceId);

  it('slides a tab to the requested position', () => {
    const res = reorderTab(threeTabs(0), 'a1', 0, 2)!;
    expect(ids(res)).toEqual(['scratch.note#2', 'scratch.note#3', 'scratch.note#1']);
  });

  it('moves leftward too', () => {
    const res = reorderTab(threeTabs(0), 'a1', 2, 0)!;
    expect(ids(res)).toEqual(['scratch.note#3', 'scratch.note#1', 'scratch.note#2']);
  });

  it('keeps the same pane on screen when a background tab moves past it', () => {
    // The bug this exists to prevent: `activeTab` is an index, so dragging tab 0
    // to the end while tab 1 is showing silently switches the visible pane.
    const res = reorderTab(threeTabs(1), 'a1', 0, 2)!;
    const area = res as AreaNode;
    expect(area.tabs[area.activeTab].instanceId).toBe('scratch.note#2');
    expect(area.activeTab).toBe(0);
  });

  it('follows the dragged tab when it is the active one', () => {
    const res = reorderTab(threeTabs(0), 'a1', 0, 2) as AreaNode;
    expect(res.activeTab).toBe(2);
    expect(res.tabs[res.activeTab].instanceId).toBe('scratch.note#1');
  });

  it('refuses a no-op or an out-of-range index', () => {
    expect(reorderTab(threeTabs(0), 'a1', 1, 1)).toBeNull();
    expect(reorderTab(threeTabs(0), 'a1', 0, 3)).toBeNull();
    expect(reorderTab(threeTabs(0), 'nope', 0, 1)).toBeNull();
  });
});

describe('frame-level pane ops', () => {
  function frameWithEverything(): FrameState {
    const base = createEmptyFrame();
    const center = insertPane(base.center, base.center.id, pane('scratch.note', 1))!;
    return {
      ...base,
      center,
      docks: {
        ...base.docks,
        left: {
          ...base.docks.left,
          visible: true,
          tools: [pane('files.tree', 2)],
          activeTool: 'files.tree#2',
        },
      },
      windows: [
        {
          id: 'w10',
          area: { kind: 'area', id: 'a11', tabs: [pane('scratch.note', 3)], activeTab: 0 },
          rect: { x: 40, y: 40, w: 400, h: 300 },
          mode: 'normal',
          z: 1,
        },
      ],
      paneSeq: 12,
    };
  }

  it('finds and lists panes across center, docks, and windows', () => {
    const frame = frameWithEverything();
    expect(listPanes(frame)).toHaveLength(3);
    expect(findPaneAnywhere(frame, 'files.tree#2')?.location).toEqual({
      kind: 'dock',
      dock: 'left',
    });
    expect(findPaneAnywhere(frame, 'scratch.note#3')?.location).toEqual({
      kind: 'window',
      windowId: 'w10',
      areaId: 'a11',
    });
  });

  it('updates a pane in a dock', () => {
    const frame = frameWithEverything();
    const next = updatePaneAnywhere(frame, 'files.tree#2', (p) => ({
      ...p,
      params: { root: '/tmp' },
    }))!;
    expect(next.docks.left.tools[0].params).toEqual({ root: '/tmp' });
  });

  it('removing the last dock tool hides the dock', () => {
    const frame = frameWithEverything();
    const res = removePaneAnywhere(frame, 'files.tree#2')!;
    expect(res.frame.docks.left.tools).toHaveLength(0);
    expect(res.frame.docks.left.visible).toBe(false);
    expect(res.frame.docks.left.activeTool).toBeNull();
  });

  it("removing a window's only pane closes the window and leaves the rest intact", () => {
    const frame = frameWithEverything();
    const res = removePaneAnywhere(frame, 'scratch.note#3')!;
    expect(res.frame.windows).toHaveLength(0);
    expect(areaOfInstance(res.frame.center, 'scratch.note#1')).not.toBeNull();
  });

  it('removing one tab of a merged window keeps the window open', () => {
    const base = frameWithEverything();
    const frame = {
      ...base,
      windows: [
        {
          ...base.windows[0],
          area: {
            ...base.windows[0].area,
            tabs: [...base.windows[0].area.tabs, pane('files.tree', 4)],
            activeTab: 1,
          },
        },
      ],
    };
    const res = removePaneAnywhere(frame, 'files.tree#4')!;
    expect(res.frame.windows).toHaveLength(1);
    expect(res.frame.windows[0].area.tabs).toHaveLength(1);
    // The active index followed the removal instead of dangling past the end.
    expect(res.frame.windows[0].area.activeTab).toBe(0);
  });

  it('removal repairs focus and fullscreen pointing at the dropped area', () => {
    const { root, right } = twoAreas();
    const base = createEmptyFrame();
    const area = findArea(root, right)!;
    const frame: FrameState = {
      ...base,
      center: root,
      focusedAreaId: right,
      fullscreenAreaId: right,
      paneSeq: 10,
    };
    const res = removePaneAnywhere(frame, area.tabs[0].instanceId)!;
    expect(res.frame.focusedAreaId).toBe('a1');
    expect(res.frame.fullscreenAreaId).toBeNull();
  });
});
