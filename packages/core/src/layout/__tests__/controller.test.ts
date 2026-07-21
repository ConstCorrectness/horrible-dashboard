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
import { dockSidesOf, isDockable, openPaneInArea, openToolInDock, roleOf } from '../controller';
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
});
