import { describe, expect, it } from 'vitest';

import { withTab } from '../dock/tree';
import { createArea, reorderTab, setActiveTab } from '../model';
import type { AreaNode, PaneState } from '../types';

function pane(viewId: string, n: number): PaneState {
  return { instanceId: `${viewId}#${n}`, viewId };
}

describe('tab edits', () => {
  /** a1 holding three tabs, with `active` showing. */
  function threeTabs(active: number): AreaNode {
    let area = createArea('a1');
    for (const n of [1, 2, 3]) area = withTab(area, pane('scratch.note', n));
    return { ...area, activeTab: active };
  }
  const ids = (area: AreaNode) => area.tabs.map((t) => t.instanceId);

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
