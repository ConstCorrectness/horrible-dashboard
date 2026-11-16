/**
 * The store wrapper around the workbench reducer: loads are atomic and clean,
 * edits bump the autosave revision, ways of looking do not, and a no-op returns
 * the same snapshot.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { collectAreas } from '../dock/tree';
import { layoutStore } from '../store';
import { findPaneAnywhere, instanceId } from '../model';
import type { PaneState } from '../types';
import { current, groupOf, groupsFrame, loadFrame } from './fixtures';

/**
 * Two centre groups: an editor with a second tab, and the welcome card.
 * `editor.buffer#4`, `scratch.note#5` in a6; `dashboard.welcome#7` in a8.
 */
function load(): void {
  loadFrame(groupsFrame([['editor.buffer', 'scratch.note'], 'dashboard.welcome']), 'test');
}
const EDITOR = 'editor.buffer#4';
const NOTE = 'scratch.note#5';
const WELCOME = 'dashboard.welcome#7';

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
    expect(snap.frame.center).toMatchObject({ kind: 'area', tabs: [] });
  });

  it('LOAD_WORKSPACE swaps id+frame atomically without bumping revision', () => {
    const before = layoutStore.getSnapshot().revision;
    load();
    const snap = layoutStore.getSnapshot();
    expect(snap.hydrated).toBe(true);
    expect(snap.workspaceId).toBe('test');
    expect(snap.revision).toBe(before);
    // Loading resolves focus against what is there.
    expect(snap.frame.focusedInstanceId).toBe(EDITOR);
  });

  it('mutations bump revision; no-ops return the same snapshot', () => {
    load();
    const loaded = layoutStore.getSnapshot();
    // No-op: focusing the already-focused pane.
    expect(layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: EDITOR })).toBe(loaded);
    const next = layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: WELCOME });
    expect(next.revision).toBe(loaded.revision + 1);
    expect(next.frame.focusedInstanceId).toBe(WELCOME);
  });

  it('maximizing and presenting re-render but do not dirty the workspace', () => {
    load();
    const before = layoutStore.getSnapshot();
    const maxed = layoutStore.dispatch({ type: 'TOGGLE_MAXIMIZED', areaId: groupOf(EDITOR)!.id });
    expect(maxed).not.toBe(before);
    expect(maxed.revision).toBe(before.revision);
    const presented = layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: EDITOR });
    expect(presented.revision).toBe(before.revision);
  });
});

describe('pane actions', () => {
  it('SET_REGION patches per-instance region state', () => {
    load();
    const region = {
      open: true,
      size: 280,
      collapsed: false,
      views: ['files.tree'],
      activeView: 'files.tree',
    };
    const next = layoutStore.dispatch({
      type: 'SET_REGION',
      instanceId: EDITOR,
      position: 'right',
      region,
    });
    expect(findPaneAnywhere(next.frame, EDITOR)?.pane.regions?.right).toEqual(region);
    const cleared = layoutStore.dispatch({
      type: 'SET_REGION',
      instanceId: EDITOR,
      position: 'right',
      region: null,
    });
    expect(findPaneAnywhere(cleared.frame, EDITOR)?.pane.regions).toBeUndefined();
  });

  it('RETARGET_PANE renames in place, keeping position and regions', () => {
    load();
    const inst = findPaneAnywhere(current(), EDITOR)!;
    const region = {
      open: true,
      size: 300,
      collapsed: false,
      views: ['editor.recentNotes'],
      activeView: 'editor.recentNotes',
    };
    layoutStore.dispatch({ type: 'SET_REGION', instanceId: EDITOR, position: 'left', region });
    const next = layoutStore.dispatch({
      type: 'RETARGET_PANE',
      instanceId: EDITOR,
      newInstanceId: 'editor.buffer:note:7',
      params: { source: 'note:7' },
    });
    const moved = findPaneAnywhere(next.frame, 'editor.buffer:note:7');
    expect(findPaneAnywhere(next.frame, EDITOR)).toBeNull();
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
      instanceId: EDITOR,
      newInstanceId: WELCOME,
    });
    expect(next.frame).toBe(frame);
  });

  it('INSERT_PANE adds a tab to a group and advances the id counter', () => {
    load();
    const target = groupOf(WELCOME)!;
    const pane = freshPane('scratch.note');
    const seq = current().paneSeq;
    const next = layoutStore.dispatch({
      type: 'INSERT_PANE',
      pane,
      target: { kind: 'area', areaId: target.id },
    });
    const area = groupOf(pane.instanceId)!;
    expect(area.id).toBe(target.id);
    expect(area.activeTab).toBe(area.tabs.length - 1);
    expect(next.frame.paneSeq).toBe(seq + 1);
  });

  it('REMOVE_PANE removes a group when its last tab goes', () => {
    load();
    const welcome = groupOf(WELCOME)!;
    const next = layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: WELCOME });
    expect(collectAreas(next.frame.center).map((a) => a.id)).not.toContain(welcome.id);
  });

  it('REMOVE_PANE of one tab leaves its group open', () => {
    load();
    const editor = groupOf(EDITOR)!;
    layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: NOTE });
    const area = groupOf(EDITOR)!;
    expect(area.id).toBe(editor.id);
    expect(area.tabs).toHaveLength(1);
    expect(area.activeTab).toBe(0);
  });

  it('SET_PRESENTED takes only a pane that exists, and clears when it closes', () => {
    load();
    expect(
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: 'nope#9' }).frame
        .presentedInstanceId,
    ).toBeNull();
    expect(
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: WELCOME }).frame
        .presentedInstanceId,
    ).toBe(WELCOME);
    expect(
      layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: WELCOME }).frame.presentedInstanceId,
    ).toBeNull();
  });
});
