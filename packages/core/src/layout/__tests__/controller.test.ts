/**
 * Controller-level rules the pure model layer can't express: what `openPane`
 * focuses rather than duplicates, and how `openDocument` reuses a pane.
 *
 * Views are registered synthetically rather than imported from real modules —
 * a manifest that reaches the editor pulls in a WebSocket at import time and
 * dies without jsdom.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { setPaneDirty } from '../close-guards';
import { openDocument, openPane, roleOf, tearOffPane } from '../controller';
import { findPaneAnywhere, listPanes } from '../model';
import { layoutStore } from '../store';
import { current, loadFrame, windowsFrame } from './fixtures';

const Stub = () => null;

beforeAll(() => {
  registry.register({
    id: 'controller-test',
    title: 'Controller test',
    panels: [
      { id: 't.doc', title: 'Doc', component: Stub, role: 'document', singleton: true },
      // The notebook/browser shape: one pane per thing, identified by params.
      { id: 't.multiDoc', title: 'Multi doc', component: Stub, role: 'document' },
      { id: 't.tool', title: 'Tool', component: Stub, role: 'tool', singleton: true },
      // The terminal's shape: a non-singleton tool, one instance per shell.
      { id: 't.multiTool', title: 'Multi tool', component: Stub, role: 'tool' },
    ],
    widgets: [{ id: 't.widget', title: 'Widget', component: Stub, role: 'widget' }],
  });
});

beforeEach(() => {
  layoutStore.resetForTests();
  // `t.doc#1`: a singleton restored from an older layout, with a `#n` suffix.
  loadFrame(windowsFrame(['t.doc']), 'test');
});

const panesOf = (viewId: string) => listPanes(current()).filter((p) => p.pane.viewId === viewId);

describe('roleOf', () => {
  it('falls back per kind for an unregistered view', () => {
    expect(roleOf('t.tool')).toBe('tool');
    expect(roleOf('nope.panel')).toBe('widget');
  });
});

describe('openPane', () => {
  it('opens a new view in a window of its own, and focuses it', () => {
    const id = openPane('t.tool')!;
    const located = findPaneAnywhere(current(), id)!;
    const win = current().windows.find((w) => w.id === located.location.windowId)!;
    expect(win.area.tabs).toHaveLength(1);
    expect(current().focusedWindowId).toBe(win.id);
    expect(current().focusedInstanceId).toBe(id);
  });

  it('focuses a singleton on a second open instead of adding a twin', () => {
    const first = openPane('t.tool');
    expect(openPane('t.tool')).toBe(first);
    expect(panesOf('t.tool')).toHaveLength(1);
  });

  it('finds a singleton restored with a `#n` suffix by its view id', () => {
    expect(openPane('t.doc')).toBe('t.doc#1');
    expect(panesOf('t.doc')).toHaveLength(1);
  });

  it('opens a fresh window per call for a non-singleton', () => {
    const a = openPane('t.multiTool');
    const b = openPane('t.multiTool');
    expect(a).not.toBe(b);
    expect(panesOf('t.multiTool')).toHaveLength(2);
  });

  it('honours a caller-supplied instance id, focusing it on a second open', () => {
    expect(openPane('t.multiDoc', { instanceId: 'doc:a' })).toBe('doc:a');
    expect(openPane('t.multiDoc', { instanceId: 'doc:a' })).toBe('doc:a');
    expect(panesOf('t.multiDoc')).toHaveLength(1);
  });

  it('returns null for a view that does not exist', () => {
    expect(openPane('nope.view')).toBeNull();
  });
});

describe('opening an already-open pane with params', () => {
  /*
   * The silent half of `openPane`. Focusing an open singleton used to discard the
   * params it was handed, so every deep link — "compare these runs", "score this
   * checkpoint" — worked only while its target pane happened to be CLOSED. Nothing
   * threw, nothing warned; the pane just came to the front showing what it showed
   * before.
   */
  it('delivers params to a singleton that is already open', () => {
    openPane('t.doc', { params: { runNames: ['a', 'b'] } });
    expect(findPaneAnywhere(current(), 't.doc#1')!.pane.params).toEqual({ runNames: ['a', 'b'] });
  });

  it('leaves existing params alone when a caller supplies none', () => {
    // Absent params mean "no opinion", not "clear it".
    openPane('t.doc', { params: { runNames: ['a', 'b'] } });
    openPane('t.doc');
    expect(findPaneAnywhere(current(), 't.doc#1')!.pane.params).toEqual({ runNames: ['a', 'b'] });
  });
});

describe('tearOffPane', () => {
  it('moves a tab into a window of its own, and refuses a pane already alone', () => {
    loadFrame(windowsFrame([['t.doc', 't.multiDoc']]), 'test');
    const tab = current().windows[0].area.tabs[1].instanceId;
    expect(tearOffPane(tab)).toBe(true);
    expect(current().windows).toHaveLength(2);
    expect(tearOffPane(tab)).toBe(false);
  });
});

describe('openDocument', () => {
  const open = (thing: string) =>
    openDocument('t.multiDoc', `t.multiDoc:${thing}`, { thing }, () => true);

  it('focuses the pane that already holds the same thing', () => {
    expect(open('a')).toBe('t.multiDoc:a');
    expect(open('a')).toBe('t.multiDoc:a');
    expect(panesOf('t.multiDoc')).toHaveLength(1);
  });

  it('takes over a clean pane in place instead of opening a second one', () => {
    open('a');
    const windows = current().windows.length;
    expect(open('b')).toBe('t.multiDoc:b');
    expect(panesOf('t.multiDoc').map((p) => p.pane.instanceId)).toEqual(['t.multiDoc:b']);
    expect(current().windows).toHaveLength(windows);
    expect(findPaneAnywhere(current(), 't.multiDoc:b')?.pane.params).toEqual({ thing: 'b' });
  });

  it('leaves a dirty pane alone and opens a new one', () => {
    open('a');
    setPaneDirty('t.multiDoc:a', true);
    expect(open('b')).toBe('t.multiDoc:b');
    expect(panesOf('t.multiDoc')).toHaveLength(2);
    setPaneDirty('t.multiDoc:a', false);
  });

  it('without a reuse rule always opens a new pane', () => {
    openDocument('t.multiDoc', 't.multiDoc:a', { thing: 'a' });
    openDocument('t.multiDoc', 't.multiDoc:b', { thing: 'b' });
    expect(panesOf('t.multiDoc')).toHaveLength(2);
  });
});
