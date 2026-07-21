/**
 * Rail glyph derivation: membership per dock, and the four states a glyph can be
 * in. Views are registered synthetically — importing a real module manifest
 * reaches the editor's module-scope WebSocket and dies without jsdom.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { openPaneInArea, openToolInDock } from '../controller';
import { collectAreas } from '../model';
import { seedFromPreset, type FramePreset } from '../presets';
import { RAIL_SECTIONS, railEntries } from '../rail';
import { layoutStore } from '../store';

const Stub = () => null;
const KNOWN = new Set(['r.doc', 'r.left', 'r.right', 'r.bottom', 'r.promoted', 'r.plain']);

const preset: FramePreset = {
  id: 'rail',
  name: 'Rail',
  frame: {
    center: { split: 'row', children: [{ pane: 'r.doc' }, { tabs: [] }] },
    docks: {
      left: { tools: ['r.left'], visible: true },
      bottom: { tools: ['r.bottom'], visible: false },
    },
  },
};

const stateOf = (entries: ReturnType<typeof railEntries>, viewId: string) =>
  entries.find((e) => e.viewId === viewId)?.state;

beforeAll(() => {
  registry.register({
    id: 'rail-test',
    title: 'Rail test',
    panels: [
      { id: 'r.doc', title: 'Doc', component: Stub, role: 'document', singleton: true },
      { id: 'r.left', title: 'Left', component: Stub, role: 'tool', singleton: true },
      {
        id: 'r.right',
        title: 'Right',
        component: Stub,
        role: 'tool',
        defaultDock: 'right',
        singleton: true,
      },
      {
        id: 'r.bottom',
        title: 'Bottom',
        component: Stub,
        role: 'tool',
        defaultDock: 'bottom',
        singleton: true,
      },
    ],
    widgets: [
      { id: 'r.promoted', title: 'Promoted', component: Stub, role: 'widget', dockable: 'right' },
      { id: 'r.plain', title: 'Plain', component: Stub, role: 'widget' },
    ],
  });
});

beforeEach(() => {
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'rail',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
});

const frame = () => layoutStore.getSnapshot().frame;

describe('rail sections', () => {
  it('gives the bottom dock to the left rail, so its tools stay switchable', () => {
    // The bottom edge is the minibuffer, not a rail — without this the bottom
    // dock would have no tool switcher at all.
    expect(RAIL_SECTIONS.left).toContain('bottom');
    expect(RAIL_SECTIONS.right).toEqual(['right']);
    expect(stateOf(railEntries(frame(), 'bottom'), 'r.bottom')).toBe('docked');
  });
});

describe('rail membership', () => {
  it('puts a view on the rail for the side it declared', () => {
    expect(railEntries(frame(), 'left').map((e) => e.viewId)).toContain('r.left');
    expect(railEntries(frame(), 'right').map((e) => e.viewId)).toContain('r.right');
    expect(railEntries(frame(), 'left').map((e) => e.viewId)).not.toContain('r.right');
  });

  it('lists a promoted widget on its declared side', () => {
    expect(railEntries(frame(), 'right').map((e) => e.viewId)).toContain('r.promoted');
  });

  it('never lists a view that is not dockable at all', () => {
    for (const side of ['left', 'right', 'bottom'] as const) {
      expect(railEntries(frame(), side).map((e) => e.viewId)).not.toContain('r.plain');
      expect(railEntries(frame(), side).map((e) => e.viewId)).not.toContain('r.doc');
    }
  });

  it('lists a docked tool once, on the side it actually sits on', () => {
    const left = railEntries(frame(), 'left').filter((e) => e.viewId === 'r.left');
    expect(left).toHaveLength(1);
  });
});

describe('rail states', () => {
  it('marks the visible tool active and a hidden dock’s tool merely docked', () => {
    expect(stateOf(railEntries(frame(), 'left'), 'r.left')).toBe('active');
    expect(stateOf(railEntries(frame(), 'bottom'), 'r.bottom')).toBe('docked');
  });

  it('marks a stacked-but-not-current tool docked, not active', () => {
    openToolInDock('r.promoted', 'right');
    openToolInDock('r.right', 'right');
    const right = railEntries(frame(), 'right');
    // The second insert becomes the dock's active tool.
    expect(stateOf(right, 'r.right')).toBe('active');
    expect(stateOf(right, 'r.promoted')).toBe('docked');
  });

  it('marks a view open out in the center as center, not closed', () => {
    const empty = collectAreas(frame().center).find((a) => a.tabs.length === 0)!;
    openPaneInArea('r.promoted', empty.id);
    expect(stateOf(railEntries(frame(), 'right'), 'r.promoted')).toBe('center');
  });

  it('marks a never-opened view closed', () => {
    expect(stateOf(railEntries(frame(), 'right'), 'r.right')).toBe('closed');
  });

  it('carries an instanceId for everything except closed', () => {
    const entries = [...railEntries(frame(), 'left'), ...railEntries(frame(), 'right')];
    for (const e of entries) {
      if (e.state === 'closed') expect(e.instanceId).toBeUndefined();
      else expect(e.instanceId).toBeTruthy();
    }
  });
});
