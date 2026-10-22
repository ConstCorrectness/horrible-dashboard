import { describe, expect, it } from 'vitest';

import {
  createArea,
  createEmptyFrame,
  findAreaAnywhere,
  findPaneAnywhere,
  insertPane,
  listPanes,
  removePaneAnywhere,
  reorderTab,
  setActiveTab,
  updateAreaAnywhere,
  updatePaneAnywhere,
  visiblePanes,
} from '../model';
import type { AreaNode, FrameState, PaneState } from '../types';

function pane(viewId: string, n: number): PaneState {
  return { instanceId: `${viewId}#${n}`, viewId };
}

describe('tab edits', () => {
  /** a1 holding three tabs, with `active` showing. */
  function threeTabs(active: number): AreaNode {
    let area = createArea('a1');
    for (const n of [1, 2, 3]) area = insertPane(area, pane('scratch.note', n));
    return { ...area, activeTab: active };
  }
  const ids = (area: AreaNode) => area.tabs.map((t) => t.instanceId);

  it('insertPane appends and activates unless told not to', () => {
    const area = threeTabs(0);
    expect(insertPane(area, pane('files.tree', 4)).activeTab).toBe(3);
    expect(insertPane(area, pane('files.tree', 4), { activate: false }).activeTab).toBe(0);
  });

  it('setActiveTab refuses an out-of-range index', () => {
    expect(setActiveTab(threeTabs(0), 2)?.activeTab).toBe(2);
    expect(setActiveTab(threeTabs(0), 3)).toBeNull();
  });

  it('reorderTab slides a tab to the requested position', () => {
    const res = reorderTab(threeTabs(0), 0, 2)!;
    expect(ids(res)).toEqual(['scratch.note#2', 'scratch.note#3', 'scratch.note#1']);
  });

  it('reorderTab moves leftward too', () => {
    const res = reorderTab(threeTabs(0), 2, 0)!;
    expect(ids(res)).toEqual(['scratch.note#3', 'scratch.note#1', 'scratch.note#2']);
  });

  it('keeps the same pane on screen when a background tab moves past it', () => {
    // The bug this exists to prevent: `activeTab` is an index, so dragging tab 0
    // to the end while tab 1 is showing silently switches the visible pane.
    const res = reorderTab(threeTabs(1), 0, 2)!;
    expect(res.tabs[res.activeTab].instanceId).toBe('scratch.note#2');
    expect(res.activeTab).toBe(0);
  });

  it('follows the dragged tab when it is the active one', () => {
    const res = reorderTab(threeTabs(0), 0, 2)!;
    expect(res.activeTab).toBe(2);
    expect(res.tabs[res.activeTab].instanceId).toBe('scratch.note#1');
  });

  it('refuses a no-op or an out-of-range index', () => {
    expect(reorderTab(threeTabs(0), 1, 1)).toBeNull();
    expect(reorderTab(threeTabs(0), 0, 3)).toBeNull();
  });
});

describe('frame-level pane ops', () => {
  function twoWindows(): FrameState {
    return {
      ...createEmptyFrame(),
      windows: [
        {
          id: 'w10',
          area: {
            kind: 'area',
            id: 'a11',
            tabs: [pane('scratch.note', 1), pane('files.tree', 2)],
            activeTab: 0,
          },
          rect: { x: 40, y: 40, w: 400, h: 300 },
          mode: 'normal',
          z: 1,
        },
        {
          id: 'w12',
          area: { kind: 'area', id: 'a13', tabs: [pane('scratch.note', 3)], activeTab: 0 },
          rect: { x: 80, y: 80, w: 400, h: 300 },
          mode: 'minimized',
          z: 2,
        },
      ],
      paneSeq: 14,
    };
  }

  it('finds and lists panes in every window', () => {
    const frame = twoWindows();
    expect(listPanes(frame)).toHaveLength(3);
    expect(findPaneAnywhere(frame, 'files.tree#2')?.location).toEqual({
      kind: 'window',
      windowId: 'w10',
      areaId: 'a11',
    });
    expect(findAreaAnywhere(frame, 'a13')?.window.id).toBe('w12');
    expect(findAreaAnywhere(frame, 'a99')).toBeNull();
  });

  it('visiblePanes is each showing window’s active tab', () => {
    // The background tab and the minimized window are both out of sight.
    expect(visiblePanes(twoWindows()).map((p) => p.pane.instanceId)).toEqual(['scratch.note#1']);
  });

  it('updates a pane and an area wherever they live', () => {
    const frame = twoWindows();
    const paned = updatePaneAnywhere(frame, 'files.tree#2', (p) => ({
      ...p,
      params: { root: '/tmp' },
    }))!;
    expect(paned.windows[0].area.tabs[1].params).toEqual({ root: '/tmp' });
    const area = updateAreaAnywhere(frame, 'a13', (a) => ({ ...a, headerCollapsed: true }))!;
    expect(area.windows[1].area.headerCollapsed).toBe(true);
    expect(updatePaneAnywhere(frame, 'nope#1', (p) => p)).toBeNull();
  });

  it("removing a window's only pane closes the window and leaves the rest intact", () => {
    const frame = { ...twoWindows(), focusedWindowId: 'w12' };
    const res = removePaneAnywhere(frame, 'scratch.note#3')!;
    expect(res.frame.windows.map((w) => w.id)).toEqual(['w10']);
    expect(res.frame.focusedWindowId).toBeNull();
    expect(res.removed.instanceId).toBe('scratch.note#3');
  });

  it('removing one tab of a merged window keeps the window open', () => {
    const base = twoWindows();
    const frame = {
      ...base,
      windows: [{ ...base.windows[0], area: { ...base.windows[0].area, activeTab: 1 } }],
    };
    const res = removePaneAnywhere(frame, 'files.tree#2')!;
    expect(res.frame.windows).toHaveLength(1);
    expect(res.frame.windows[0].area.tabs).toHaveLength(1);
    // The active index followed the removal instead of dangling past the end.
    expect(res.frame.windows[0].area.activeTab).toBe(0);
  });
});
