/**
 * `arrangeCenter`: the workbench's "lay everything out". It rebuilds the centre
 * tree and nothing else — docks and popouts are where the user put them.
 */
import { describe, expect, it } from 'vitest';

import type { PaneState } from '../../types';
import { arrangeCenter } from '../arrange';
import { createEmptyDockFrame, findPane, insertPane, listPanes } from '../frame';
import { dockReduce } from '../reducer';
import { collectAreas } from '../tree';
import type { DockFrame, PlaceTarget, SplitNode } from '../types';

const pane = (id: string): PaneState => ({ instanceId: id, viewId: id.split('#')[0] });

function build(...steps: [string, PlaceTarget][]): DockFrame {
  return steps.reduce(
    (frame, [id, target]) => insertPane(frame, pane(id), target)!,
    createEmptyDockFrame(),
  );
}

/** Three documents as tabs of one group, plus a terminal in the bottom dock. */
const tabbed = () =>
  build(
    ['a#1', { kind: 'center' }],
    ['b#2', { kind: 'center' }],
    ['c#3', { kind: 'center' }],
    ['term#4', { kind: 'dock', side: 'bottom' }],
  );

const groupsOf = (frame: DockFrame) =>
  collectAreas(frame.center).map((a) => a.tabs.map((t) => t.instanceId));

describe('arrangeCenter', () => {
  it('columns: one group per centre pane, side by side, equal shares', () => {
    const frame = arrangeCenter(tabbed(), 'columns');
    expect(groupsOf(frame)).toEqual([['a#1'], ['b#2'], ['c#3']]);
    const root = frame.center as SplitNode;
    expect(root.orientation).toBe('row');
    for (const s of root.sizes) expect(s).toBeCloseTo(1 / 3);
  });

  it('rows stack the groups', () => {
    const root = arrangeCenter(tabbed(), 'rows').center as SplitNode;
    expect(root.orientation).toBe('column');
    expect(root.children).toHaveLength(3);
  });

  it('grid: rows of ceil(sqrt(n)) groups', () => {
    let frame = tabbed();
    frame = insertPane(frame, pane('d#9'), { kind: 'center' })!;
    const root = arrangeCenter(frame, 'grid').center as SplitNode;
    expect(root.orientation).toBe('column');
    expect(root.children.map((c) => (c.kind === 'split' ? c.children.length : 1))).toEqual([2, 2]);
  });

  it('tabs gathers every centre pane into one group, keeping the focused pane showing', () => {
    let frame = arrangeCenter(tabbed(), 'columns');
    frame = dockReduce(frame, { type: 'FOCUS_PANE', instanceId: 'b#2' });
    const merged = arrangeCenter(frame, 'tabs');
    expect(groupsOf(merged)).toEqual([['a#1', 'b#2', 'c#3']]);
    const area = collectAreas(merged.center)[0];
    expect(area.tabs[area.activeTab].instanceId).toBe('b#2');
    expect(merged.focusedInstanceId).toBe('b#2');
  });

  it('even resets every split to equal shares and is a no-op when already even', () => {
    const cols = arrangeCenter(tabbed(), 'columns');
    const skewed = dockReduce(cols, {
      type: 'SET_SPLIT_SIZES',
      splitId: (cols.center as SplitNode).id,
      sizes: [0.6, 0.2, 0.2],
    });
    const even = arrangeCenter(skewed, 'even');
    for (const s of (even.center as SplitNode).sizes) expect(s).toBeCloseTo(1 / 3);
    expect(arrangeCenter(even, 'even')).toBe(even);
  });

  it('leaves docks alone and never loses a pane', () => {
    const before = tabbed();
    for (const style of ['columns', 'rows', 'grid', 'tabs', 'even'] as const) {
      const after = arrangeCenter(before, style);
      expect(findPane(after, 'term#4')!.location).toMatchObject({ kind: 'dock', side: 'bottom' });
      expect(
        listPanes(after)
          .map((p) => p.pane.instanceId)
          .sort(),
      ).toEqual(
        listPanes(before)
          .map((p) => p.pane.instanceId)
          .sort(),
      );
    }
  });

  it('is idempotent: the same arrangement twice returns the same frame', () => {
    const once = arrangeCenter(tabbed(), 'columns');
    expect(arrangeCenter(once, 'columns')).toBe(once);
  });

  it('keeps the existing group id on its first pane', () => {
    const before = tabbed();
    const id = collectAreas(before.center)[0].id;
    const after = arrangeCenter(before, 'columns');
    expect(findPane(after, 'a#1')!.location.areaId).toBe(id);
  });

  it('clears a maximize, since the point is to see everything', () => {
    let frame = arrangeCenter(tabbed(), 'columns');
    frame = dockReduce(frame, {
      type: 'TOGGLE_MAXIMIZED',
      areaId: findPane(frame, 'a#1')!.location.areaId,
    });
    expect(frame.maximizedAreaId).not.toBeNull();
    expect(dockReduce(frame, { type: 'ARRANGE_CENTER', style: 'tabs' }).maximizedAreaId).toBeNull();
  });

  it('an empty centre has nothing to arrange', () => {
    const empty = createEmptyDockFrame();
    expect(arrangeCenter(empty, 'grid')).toBe(empty);
  });
});
