/**
 * The workbench's tab model.
 *
 * It lives in the host pane's `params` — which is what makes it survive an
 * unmount (only an area's active tab renders, so the workbench unmounts whenever
 * you look at another document) and a reload (params are serialized into the
 * workspace blob). These tests drive it through the real `layoutStore` for that
 * reason: a mock would not catch the thing most likely to go wrong, which is
 * that `SET_PANE_PARAMS` **replaces** rather than merges.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { openPane } from '../../../layout/controller';
import { findPaneAnywhere } from '../../../layout/model';
import { layoutStore } from '../../../layout/store';
import { registry } from '../../../registry';
import { activateTab, closeTab, moveTab, openTab, readTabs, renameTab } from '../openBuffers';

const Stub = () => null;
const A = 'workspace-file:/repo/a.ts';
const B = 'workspace-file:/repo/b.ts';
const C = 'workspace-file:/repo/c.ts';

registry.register({
  id: 'ide-tabs-test',
  title: 'IDE tabs test',
  // A stand-in for `ide.workbench`: importing the real manifest would reach the
  // editor and its module-scope WebSocket.
  panels: [{ id: 'test.workbench', title: 'Workbench', component: Stub, role: 'document' }],
});

let host: string;

beforeEach(() => {
  layoutStore.resetForTests();
  host = openPane('test.workbench')!;
  // Something else the pane was opened with, to prove writes preserve it.
  layoutStore.dispatch({
    type: 'SET_PANE_PARAMS',
    instanceId: host,
    params: { keepMe: 'yes', tabs: [], active: null },
  });
});

function params() {
  return findPaneAnywhere(layoutStore.getSnapshot().frame, host)?.pane.params ?? {};
}

describe('open and activate', () => {
  it('opens tabs in order and activates the newest', () => {
    openTab(host, A);
    openTab(host, B);
    expect(readTabs(host)).toEqual({ tabs: [A, B], active: B });
  });

  it('activates an already-open file instead of duplicating it', () => {
    openTab(host, A);
    openTab(host, B);
    openTab(host, A);
    expect(readTabs(host)).toEqual({ tabs: [A, B], active: A });
  });

  it('ignores an activate for a file that is not open', () => {
    openTab(host, A);
    activateTab(host, B);
    expect(readTabs(host).active).toBe(A);
  });

  it('preserves the pane’s other params', () => {
    // `SET_PANE_PARAMS` replaces the whole object, so a mutator that forgets to
    // spread silently drops the title the pane was opened with.
    openTab(host, A);
    expect(params().keepMe).toBe('yes');
  });
});

describe('close', () => {
  it('activates the neighbour, not the first tab', () => {
    openTab(host, A);
    openTab(host, B);
    openTab(host, C);
    activateTab(host, B);
    closeTab(host, B);
    // The tab to its right. Falling back to index 0 would throw you to the far
    // end of the strip every time you closed something.
    expect(readTabs(host)).toEqual({ tabs: [A, C], active: C });
  });

  it('falls back to the new last tab when the rightmost closes', () => {
    openTab(host, A);
    openTab(host, B);
    closeTab(host, B);
    expect(readTabs(host)).toEqual({ tabs: [A], active: A });
  });

  it('leaves the active tab alone when a different one closes', () => {
    openTab(host, A);
    openTab(host, B);
    closeTab(host, A);
    expect(readTabs(host)).toEqual({ tabs: [B], active: B });
  });

  it('empties cleanly', () => {
    openTab(host, A);
    closeTab(host, A);
    expect(readTabs(host)).toEqual({ tabs: [], active: null });
  });
});

describe('move and rename', () => {
  it('reorders without changing which tab is active', () => {
    openTab(host, A);
    openTab(host, B);
    openTab(host, C);
    moveTab(host, C, 0);
    expect(readTabs(host)).toEqual({ tabs: [C, A, B], active: C });
  });

  it('renames in place, keeping position and active state', () => {
    // Save As on an untitled buffer. Closing and reopening would move the tab
    // to the end, which is not what saving a file should look like.
    openTab(host, A);
    openTab(host, B);
    activateTab(host, A);
    expect(renameTab(host, A, C)).toBe(true);
    expect(readTabs(host)).toEqual({ tabs: [C, B], active: C });
  });

  it('collapses a rename onto a file that is already open', () => {
    openTab(host, A);
    openTab(host, B);
    renameTab(host, A, B);
    expect(readTabs(host).tabs).toEqual([B]);
  });

  it('refuses to rename a tab it does not hold', () => {
    expect(renameTab(host, A, B)).toBe(false);
  });
});

describe('a closed workbench', () => {
  it('reads empty rather than throwing', () => {
    expect(readTabs('no-such-pane')).toEqual({ tabs: [], active: null });
    // And a write against one is a no-op, not a crash.
    openTab('no-such-pane', A);
    expect(readTabs('no-such-pane').tabs).toEqual([]);
  });
});
