import { beforeEach, describe, expect, it } from 'vitest';

import { layoutStore } from '../store';
import { areaId, findPaneAnywhere, instanceId, windowId } from '../model';
import type { PaneState } from '../types';
import { current, loadFrame, windowsFrame } from './fixtures';

/** Two windows: an editor with a second tab, and the welcome card. */
function load(): void {
  loadFrame(windowsFrame([['editor.buffer', 'scratch.note'], 'dashboard.welcome']), 'test');
}

function freshPane(viewId: string): PaneState {
  return { instanceId: instanceId(viewId, current().paneSeq), viewId };
}

beforeEach(() => {
  layoutStore.resetForTests();
});

describe('hydration and revisions', () => {
  it('starts unhydrated with a valid empty frame', () => {
    const snap = layoutStore.getSnapshot();
    expect(snap.hydrated).toBe(false);
    expect(snap.workspaceId).toBeNull();
    expect(snap.frame.windows).toEqual([]);
  });

  it('LOAD_WORKSPACE swaps id+frame atomically without bumping revision', () => {
    const before = layoutStore.getSnapshot().revision;
    load();
    const snap = layoutStore.getSnapshot();
    expect(snap.hydrated).toBe(true);
    expect(snap.workspaceId).toBe('test');
    expect(snap.revision).toBe(before);
  });

  it('mutations bump revision; no-ops return the same snapshot', () => {
    load();
    const [editor, welcome] = current().windows;
    const focused = layoutStore.dispatch({
      type: 'FOCUS_PANE',
      instanceId: editor.area.tabs[0].instanceId,
    });
    // No-op: focusing the already-focused pane.
    expect(
      layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor.area.tabs[0].instanceId }),
    ).toBe(focused);
    const next = layoutStore.dispatch({
      type: 'FOCUS_PANE',
      instanceId: welcome.area.tabs[0].instanceId,
    });
    expect(next.revision).toBe(focused.revision + 1);
    expect(next.frame.focusedInstanceId).toBe(welcome.area.tabs[0].instanceId);
  });
});

describe('pane actions', () => {
  it('SET_REGION patches per-instance region state', () => {
    load();
    const inst = current().windows[0].area.tabs[0].instanceId;
    const region = {
      open: true,
      size: 280,
      collapsed: false,
      views: ['files.tree'],
      activeView: 'files.tree',
    };
    const next = layoutStore.dispatch({
      type: 'SET_REGION',
      instanceId: inst,
      position: 'right',
      region,
    });
    expect(findPaneAnywhere(next.frame, inst)?.pane.regions?.right).toEqual(region);
    const cleared = layoutStore.dispatch({
      type: 'SET_REGION',
      instanceId: inst,
      position: 'right',
      region: null,
    });
    expect(findPaneAnywhere(cleared.frame, inst)?.pane.regions).toBeUndefined();
  });

  it('RETARGET_PANE renames in place, keeping position and regions', () => {
    load();
    const inst = findPaneAnywhere(current(), current().windows[0].area.tabs[0].instanceId)!;
    const region = {
      open: true,
      size: 300,
      collapsed: false,
      views: ['editor.recentNotes'],
      activeView: 'editor.recentNotes',
    };
    layoutStore.dispatch({
      type: 'SET_REGION',
      instanceId: inst.pane.instanceId,
      position: 'left',
      region,
    });
    const next = layoutStore.dispatch({
      type: 'RETARGET_PANE',
      instanceId: inst.pane.instanceId,
      newInstanceId: 'editor.buffer:note:7',
      params: { source: 'note:7' },
    });
    const moved = findPaneAnywhere(next.frame, 'editor.buffer:note:7');
    expect(findPaneAnywhere(next.frame, inst.pane.instanceId)).toBeNull();
    expect(moved?.location).toEqual(inst.location);
    expect(moved?.pane.viewId).toBe('editor.buffer');
    expect(moved?.pane.params).toEqual({ source: 'note:7' });
    expect(moved?.pane.regions?.left).toEqual(region);
  });

  it('RETARGET_PANE refuses to mint an id another pane already holds', () => {
    load();
    const frame = current();
    const next = layoutStore.dispatch({
      type: 'RETARGET_PANE',
      instanceId: frame.windows[0].area.tabs[0].instanceId,
      newInstanceId: frame.windows[1].area.tabs[0].instanceId,
    });
    expect(next.frame).toBe(frame);
  });

  it('INSERT_PANE adds a tab to a window and advances the id counter', () => {
    load();
    const win = current().windows[1];
    const pane = freshPane('scratch.note');
    const seq = current().paneSeq;
    const next = layoutStore.dispatch({ type: 'INSERT_PANE', areaId: win.area.id, pane });
    const area = next.frame.windows.find((w) => w.id === win.id)!.area;
    expect(area.tabs.map((t) => t.instanceId)).toContain(pane.instanceId);
    expect(area.activeTab).toBe(area.tabs.length - 1);
    expect(next.frame.paneSeq).toBe(seq + 1);
  });

  it('REMOVE_PANE closes a window when its last tab goes', () => {
    load();
    const welcome = current().windows[1];
    layoutStore.dispatch({ type: 'FOCUS_WINDOW', windowId: welcome.id });
    const next = layoutStore.dispatch({
      type: 'REMOVE_PANE',
      instanceId: welcome.area.tabs[0].instanceId,
    });
    expect(next.frame.windows.map((w) => w.id)).not.toContain(welcome.id);
    expect(next.frame.focusedWindowId).toBeNull();
  });

  it('REMOVE_PANE of one tab leaves its window open', () => {
    load();
    const editor = current().windows[0];
    const next = layoutStore.dispatch({
      type: 'REMOVE_PANE',
      instanceId: editor.area.tabs[1].instanceId,
    });
    const win = next.frame.windows.find((w) => w.id === editor.id)!;
    expect(win.area.tabs).toHaveLength(1);
    expect(win.area.activeTab).toBe(0);
  });
});

describe('window actions', () => {
  it('OPEN_WINDOW mints precomputable ids and stacks the window on top', () => {
    load();
    const seq = current().paneSeq;
    const pane = { instanceId: instanceId('scratch.note', seq), viewId: 'scratch.note' };
    const next = layoutStore.dispatch({ type: 'OPEN_WINDOW', pane });
    const win = next.frame.windows.at(-1)!;
    expect(win.id).toBe(windowId(seq + 1));
    expect(win.area.id).toBe(areaId(seq + 2));
    expect(win.area.tabs).toEqual([pane]);
    expect(win.z).toBe(Math.max(...next.frame.windows.map((w) => w.z)));
    expect(next.frame.focusedWindowId).toBe(win.id);
    expect(next.frame.paneSeq).toBe(seq + 3);
  });

  it('OPEN_WINDOW refuses a pane that is already open', () => {
    load();
    const frame = current();
    const pane = frame.windows[1].area.tabs[0];
    expect(layoutStore.dispatch({ type: 'OPEN_WINDOW', pane }).frame).toBe(frame);
  });

  it('WINDOW_FROM_PANE tears a tab out into a window of its own', () => {
    load();
    const editor = current().windows[0];
    const tab = editor.area.tabs[1].instanceId;
    const next = layoutStore.dispatch({ type: 'WINDOW_FROM_PANE', instanceId: tab });
    expect(next.frame.windows).toHaveLength(3);
    const located = findPaneAnywhere(next.frame, tab)!;
    expect(located.location.windowId).not.toBe(editor.id);
    expect(next.frame.windows.find((w) => w.id === editor.id)!.area.tabs).toHaveLength(1);
  });

  it('WINDOW_FROM_PANE is a no-op for a pane already alone in its window', () => {
    load();
    const frame = current();
    const alone = frame.windows[1].area.tabs[0].instanceId;
    expect(layoutStore.dispatch({ type: 'WINDOW_FROM_PANE', instanceId: alone }).frame).toBe(frame);
  });

  it('SET_PRESENTED takes only a pane that exists, and clears when it closes', () => {
    load();
    const welcome = current().windows[1].area.tabs[0].instanceId;
    expect(
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: 'nope#9' }).frame
        .presentedInstanceId,
    ).toBeNull();
    expect(
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: welcome }).frame
        .presentedInstanceId,
    ).toBe(welcome);
    expect(
      layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: welcome }).frame.presentedInstanceId,
    ).toBeNull();
  });
});
