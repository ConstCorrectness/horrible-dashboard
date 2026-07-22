/**
 * Controller-level rules that the pure model layer can't express: which docks a
 * view may be toggled into (`dockSidesOf`), and the relaxed zone guards that let
 * a dockable widget earn a rail glyph while a center area hosts anything.
 *
 * Views are registered synthetically rather than imported from real modules —
 * a manifest that reaches the editor pulls in a WebSocket at import time and
 * dies without jsdom.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { setPaneDirty } from '../close-guards';
import {
  areaHostingView,
  dockSidesOf,
  isDockable,
  openDocument,
  openPane,
  openPaneInArea,
  openToolInDock,
  roleOf,
} from '../controller';
import { collectAreas, findPaneAnywhere } from '../model';
import { seedFromPreset, type FramePreset } from '../presets';
import { layoutStore } from '../store';

const Stub = () => null;

const preset: FramePreset = {
  id: 'test',
  name: 'Test',
  frame: { center: { split: 'row', children: [{ pane: 't.doc' }, { tabs: [] }] } },
};

beforeAll(() => {
  registry.register({
    id: 'dockable-test',
    title: 'Dockable test',
    panels: [
      { id: 't.doc', title: 'Doc', component: Stub, role: 'document', singleton: true },
      // The notebook/browser shape: one pane per thing, identified by params.
      { id: 't.multiDoc', title: 'Multi doc', component: Stub, role: 'document' },
      // A tool with no defaultDock: the derivation must fall back to `left`.
      { id: 't.tool', title: 'Tool', component: Stub, role: 'tool', singleton: true },
      {
        id: 't.rightTool',
        title: 'Right tool',
        component: Stub,
        role: 'tool',
        defaultDock: 'right',
        singleton: true,
      },
    ],
    widgets: [
      { id: 't.widget', title: 'Widget', component: Stub, role: 'widget' },
      // The phase-1 promotion shape: a widget that opts into a rail.
      { id: 't.promoted', title: 'Promoted', component: Stub, role: 'widget', dockable: 'right' },
      {
        id: 't.multi',
        title: 'Multi',
        component: Stub,
        role: 'widget',
        dockable: ['bottom', 'left'],
      },
    ],
  });
});

beforeEach(() => {
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'test',
    frame: seedFromPreset(preset, {
      knownViews: new Set(['t.doc', 't.tool', 't.rightTool', 't.widget', 't.promoted', 't.multi']),
    }),
  });
});

describe('dockSidesOf', () => {
  it('derives a tool’s side from defaultDock, defaulting to left', () => {
    expect(dockSidesOf('t.tool')).toEqual(['left']);
    expect(dockSidesOf('t.rightTool')).toEqual(['right']);
  });

  it('treats an undeclared document or widget as center-only', () => {
    expect(dockSidesOf('t.doc')).toEqual([]);
    expect(dockSidesOf('t.widget')).toEqual([]);
    expect(isDockable('t.widget')).toBe(false);
  });

  it('honors an explicit dockable, preferred side first', () => {
    expect(dockSidesOf('t.promoted')).toEqual(['right']);
    expect(dockSidesOf('t.multi')).toEqual(['bottom', 'left']);
    expect(isDockable('t.promoted')).toBe(true);
  });

  it('leaves a promoted widget’s default placement in the center', () => {
    // The whole point of keeping role separate: dockable adds a home, it does
    // not move the default one.
    expect(roleOf('t.promoted')).toBe('widget');
  });

  it('returns nothing for an unregistered view', () => {
    expect(dockSidesOf('nope.missing')).toEqual([]);
  });
});

describe('openToolInDock', () => {
  it('docks a promoted widget on its declared side', () => {
    const id = openToolInDock('t.promoted');
    expect(id).not.toBeNull();
    expect(findPaneAnywhere(layoutStore.getSnapshot().frame, id!)?.location).toEqual({
      kind: 'dock',
      dock: 'right',
    });
  });

  it('refuses a view that never opted in', () => {
    expect(openToolInDock('t.widget')).toBeNull();
    expect(openToolInDock('t.doc')).toBeNull();
  });

  it('refuses a side the view did not declare', () => {
    expect(openToolInDock('t.promoted', 'left')).toBeNull();
    expect(openToolInDock('t.multi', 'left')).not.toBeNull();
  });
});

describe('openPaneInArea', () => {
  it('lands a tool in the chosen area instead of routing it to a dock', () => {
    const empty = collectAreas(layoutStore.getSnapshot().frame.center).find(
      (a) => a.tabs.length === 0,
    )!;
    const id = openPaneInArea('t.tool', empty.id);
    expect(id).not.toBeNull();
    expect(findPaneAnywhere(layoutStore.getSnapshot().frame, id!)?.location).toEqual({
      kind: 'area',
      areaId: empty.id,
    });
  });

  it('focuses an existing singleton rather than duplicating it', () => {
    const empty = collectAreas(layoutStore.getSnapshot().frame.center).find(
      (a) => a.tabs.length === 0,
    )!;
    // The seeded instance carries a `#n` suffix; the call must return THAT id.
    expect(openPaneInArea('t.doc', empty.id)).toBe('t.doc#0');
    const panes = collectAreas(layoutStore.getSnapshot().frame.center).flatMap((a) => a.tabs);
    expect(panes.filter((p) => p.viewId === 't.doc')).toHaveLength(1);
  });

  it('rejects an unknown area or view', () => {
    expect(openPaneInArea('t.tool', 'nope')).toBeNull();
    expect(openPaneInArea('nope.missing', 'a0')).toBeNull();
  });

  it('honours a caller-supplied instance id, focusing it on a second open', () => {
    const empty = collectAreas(layoutStore.getSnapshot().frame.center).find(
      (a) => a.tabs.length === 0,
    )!;
    expect(openPaneInArea('t.multiDoc', empty.id, { n: 1 }, 'doc:a')).toBe('doc:a');
    // Same identity again: focus, don't add a second tab.
    expect(openPaneInArea('t.multiDoc', empty.id, { n: 1 }, 'doc:a')).toBe('doc:a');
    expect(openPaneInArea('t.multiDoc', empty.id, { n: 2 }, 'doc:b')).toBe('doc:b');
    const area = collectAreas(layoutStore.getSnapshot().frame.center).find(
      (a) => a.id === empty.id,
    )!;
    expect(area.tabs.map((t) => t.instanceId)).toEqual(['doc:a', 'doc:b']);
  });
});

describe('openPane', () => {
  it('focuses a preset-seeded singleton rather than splitting a duplicate', () => {
    // The seeded instance carries a `#n` suffix, so the instance-id lookup
    // alone misses it — the games "Join splits the window" bug.
    expect(openPane('t.doc')).toBe('t.doc#0');
    const panes = collectAreas(layoutStore.getSnapshot().frame.center).flatMap((a) => a.tabs);
    expect(panes.filter((p) => p.viewId === 't.doc')).toHaveLength(1);
  });
});

describe('areaHostingView', () => {
  it('finds the area a view already occupies, so siblings can tab into it', () => {
    const seeded = findPaneAnywhere(layoutStore.getSnapshot().frame, 't.doc#0')!;
    expect(seeded.location).toMatchObject({ kind: 'area' });
    const areaIdOfSeeded = (seeded.location as { areaId: string }).areaId;
    expect(areaHostingView('t.doc')).toBe(areaIdOfSeeded);
  });

  it('is null when the view is nowhere in the center', () => {
    expect(areaHostingView('t.multiDoc')).toBeNull();
    // A docked tool is not a center area, so it must not be offered as a host.
    openToolInDock('t.rightTool');
    expect(areaHostingView('t.rightTool')).toBeNull();
  });
});

describe('openDocument', () => {
  const docPanes = () =>
    collectAreas(layoutStore.getSnapshot().frame.center)
      .flatMap((a) => a.tabs)
      .filter((p) => p.viewId === 't.multiDoc');

  const open = (thing: string) =>
    openDocument('t.multiDoc', `t.multiDoc:${thing}`, { thing }, () => true);

  it('focuses the pane that already holds the same thing', () => {
    expect(open('a')).toBe('t.multiDoc:a');
    expect(open('a')).toBe('t.multiDoc:a');
    expect(docPanes()).toHaveLength(1);
  });

  it('takes over a clean pane in place instead of splitting a second one', () => {
    open('a');
    const before = collectAreas(layoutStore.getSnapshot().frame.center).length;
    expect(open('b')).toBe('t.multiDoc:b');
    expect(docPanes().map((p) => p.instanceId)).toEqual(['t.multiDoc:b']);
    expect(collectAreas(layoutStore.getSnapshot().frame.center)).toHaveLength(before);
    expect(findPaneAnywhere(layoutStore.getSnapshot().frame, 't.multiDoc:b')?.pane.params).toEqual({
      thing: 'b',
    });
  });

  it('leaves a dirty pane alone and opens a new one', () => {
    open('a');
    setPaneDirty('t.multiDoc:a', true);
    expect(open('b')).toBe('t.multiDoc:b');
    expect(docPanes()).toHaveLength(2);
    setPaneDirty('t.multiDoc:a', false);
  });

  it('without a reuse rule always opens a new pane', () => {
    openDocument('t.multiDoc', 't.multiDoc:a', { thing: 'a' });
    openDocument('t.multiDoc', 't.multiDoc:b', { thing: 'b' });
    expect(docPanes()).toHaveLength(2);
  });
});
