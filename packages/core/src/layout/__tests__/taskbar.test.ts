/**
 * Taskbar derivation: one button per open pane, the four states, the stable
 * order, and what a click resolves to.
 *
 * Views are registered synthetically for the same reason the rail suite does it:
 * importing a real module manifest reaches the editor's module-scope WebSocket
 * and dies without jsdom.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { activateTaskbarEntry, openPaneInArea, setPaneWindowed, toggleDock } from '../controller';
import { collectAreas } from '../model';
import { seedFromPreset, type FramePreset } from '../presets';
import { layoutStore } from '../store';
import { taskbarEntries } from '../taskbar';

const Stub = () => null;
const KNOWN = new Set(['t.a', 't.b', 't.tool']);

const preset: FramePreset = {
  id: 'tb',
  name: 'Taskbar',
  frame: {
    center: { split: 'row', children: [{ pane: 't.a' }, { tabs: [] }] },
    docks: { left: { tools: ['t.tool'], visible: true } },
  },
};

beforeAll(() => {
  registry.register({
    id: 'taskbar-test',
    title: 'Taskbar test',
    panels: [
      { id: 't.a', title: 'Alpha', component: Stub, role: 'document', icon: 'A' },
      { id: 't.b', title: 'Beta', component: Stub, role: 'document', icon: 'B' },
      { id: 't.tool', title: 'Tool', component: Stub, role: 'tool', singleton: true, icon: 'T' },
    ],
  });
});

beforeEach(() => {
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'tb',
    frame: seedFromPreset(preset, { knownViews: KNOWN }),
  });
});

const frame = () => layoutStore.getSnapshot().frame;
const entry = (instanceId: string) =>
  taskbarEntries(frame()).find((e) => e.instanceId === instanceId);
const emptyArea = () => collectAreas(frame().center).find((a) => a.tabs.length === 0)!.id;

describe('membership', () => {
  it('lists every open pane once, wherever it lives', () => {
    const ids = taskbarEntries(frame()).map((e) => e.viewId);
    expect(ids).toContain('t.a');
    expect(ids).toContain('t.tool');
    expect(ids).toHaveLength(2);
  });

  it('lists each tab of a merged window separately', () => {
    // A window holding three tabs is three things to switch to. One button for
    // the window would leave two of them unreachable from the taskbar.
    const a = collectAreas(frame().center)[0].tabs[0].instanceId;
    setPaneWindowed(a, true);
    // Straight to the store: `openPaneInArea` resolves against the CENTRE tree
    // only, so it cannot address a window's area. Merging is what the drag does.
    layoutStore.dispatch({
      type: 'INSERT_PANE',
      areaId: frame().windows[0].area.id,
      pane: { instanceId: 't.b#9', viewId: 't.b' },
    });
    const win = frame().windows[0];
    expect(win.area.tabs).toHaveLength(2);
    expect(taskbarEntries(frame()).filter((e) => e.windowId === win.id)).toHaveLength(2);
  });

  it('drops a pane whose view is no longer registered rather than showing a blank', () => {
    layoutStore.dispatch({
      type: 'LOAD_WORKSPACE',
      workspaceId: 'tb',
      frame: {
        ...frame(),
        docks: { ...frame().docks, left: { ...frame().docks.left, tools: [] } },
        center: {
          kind: 'area',
          id: 'a0',
          activeTab: 0,
          tabs: [{ instanceId: 'gone#1', viewId: 'not.registered' }],
        },
      },
    });
    expect(taskbarEntries(frame())).toHaveLength(0);
  });
});

describe('states', () => {
  it('marks the visible focused centre pane focused and a background tab hidden', () => {
    const areaId = collectAreas(frame().center)[0].id;
    const a = collectAreas(frame().center)[0].tabs[0].instanceId;
    const b = openPaneInArea('t.b', areaId)!;
    expect(entry(b)!.state).toBe('focused');
    expect(entry(a)!.state).toBe('hidden');
  });

  it('marks a tool in a hidden dock hidden, and shown-but-unfocused open', () => {
    const tool = frame().docks.left.tools[0].instanceId;
    expect(entry(tool)!.state).toBe('open');
    toggleDock('left', false);
    expect(entry(tool)!.state).toBe('hidden');
  });

  it('marks a minimized window minimized — the only way back to it', () => {
    const a = collectAreas(frame().center)[0].tabs[0].instanceId;
    setPaneWindowed(a, true);
    const win = frame().windows[0];
    layoutStore.dispatch({ type: 'SET_WINDOW_MODE', windowId: win.id, mode: 'minimized' });
    expect(entry(a)!.state).toBe('minimized');
  });
});

describe('order', () => {
  it('is stable under focus, minimize and z-order changes', () => {
    const areaId = emptyArea();
    const a = collectAreas(frame().center)[0].tabs[0].instanceId;
    openPaneInArea('t.b', areaId);
    const before = taskbarEntries(frame()).map((e) => e.instanceId);

    setPaneWindowed(a, true);
    layoutStore.dispatch({
      type: 'SET_WINDOW_MODE',
      windowId: frame().windows[0].id,
      mode: 'minimized',
    });
    // A taskbar that reorders on focus moves the button out from under a pointer
    // already travelling toward it.
    expect(taskbarEntries(frame()).map((e) => e.instanceId)).toEqual(before);
  });

  it('sorts #10 after #2 rather than lexically', () => {
    const areaId = emptyArea();
    const ids: string[] = [];
    for (let i = 0; i < 10; i++)
      ids.push(openPaneInArea('t.b', areaId, undefined, `t.b#${i + 1}`)!);
    const order = taskbarEntries(frame())
      .map((e) => e.instanceId)
      .filter((id) => id.startsWith('t.b#'));
    expect(order).toEqual(ids);
  });
});

describe('activate', () => {
  it('minimizes the window you are already looking at', () => {
    const a = collectAreas(frame().center)[0].tabs[0].instanceId;
    setPaneWindowed(a, true);
    expect(entry(a)!.state).toBe('focused');
    activateTaskbarEntry(a);
    expect(entry(a)!.state).toBe('minimized');
  });

  it('restores and focuses a minimized one', () => {
    const a = collectAreas(frame().center)[0].tabs[0].instanceId;
    setPaneWindowed(a, true);
    activateTaskbarEntry(a);
    activateTaskbarEntry(a);
    expect(entry(a)!.state).toBe('focused');
  });

  it('reveals a background tab instead of hiding anything', () => {
    const areaId = collectAreas(frame().center)[0].id;
    const a = collectAreas(frame().center)[0].tabs[0].instanceId;
    openPaneInArea('t.b', areaId);
    expect(entry(a)!.state).toBe('hidden');
    activateTaskbarEntry(a);
    expect(entry(a)!.state).toBe('focused');
  });

  it('reveals a tool whose dock is closed', () => {
    const tool = frame().docks.left.tools[0].instanceId;
    toggleDock('left', false);
    activateTaskbarEntry(tool);
    expect(frame().docks.left.visible).toBe(true);
  });

  it('returns false for an unknown instance rather than throwing', () => {
    expect(activateTaskbarEntry('nope#9')).toBe(false);
  });
});
