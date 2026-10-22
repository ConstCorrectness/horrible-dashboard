/**
 * Taskbar derivation: one button per open pane, the four states, the stable
 * order, and what a click resolves to.
 *
 * Views are registered synthetically: importing a real module manifest reaches
 * the editor's module-scope WebSocket and dies without jsdom.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { activateTaskbarEntry, minimizePane, openPane } from '../controller';
import { layoutStore } from '../store';
import { taskbarEntries } from '../taskbar';
import { current, loadFrame, windowsFrame } from './fixtures';

const Stub = () => null;

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
  // `t.a#1` in the first window, `t.tool#4` in the second.
  loadFrame(windowsFrame(['t.a', 't.tool']), 'tb');
});

const entry = (instanceId: string) =>
  taskbarEntries(current()).find((e) => e.instanceId === instanceId);
const focus = (windowId: string) => layoutStore.dispatch({ type: 'FOCUS_WINDOW', windowId });

describe('membership', () => {
  it('lists every open pane once', () => {
    const ids = taskbarEntries(current()).map((e) => e.viewId);
    expect(ids).toEqual(['t.a', 't.tool']);
  });

  it('lists each tab of a merged window separately', () => {
    // A window holding three tabs is three things to switch to. One button for
    // the window would leave two of them unreachable from the taskbar.
    const win = current().windows[0];
    layoutStore.dispatch({
      type: 'INSERT_PANE',
      areaId: win.area.id,
      pane: { instanceId: 't.b#9', viewId: 't.b' },
    });
    expect(taskbarEntries(current()).filter((e) => e.windowId === win.id)).toHaveLength(2);
  });

  it('drops a pane whose view is no longer registered rather than showing a blank', () => {
    loadFrame(windowsFrame(['not.registered']), 'tb');
    expect(taskbarEntries(current())).toHaveLength(0);
  });
});

describe('states', () => {
  it('marks the focused window’s pane focused and a background tab hidden', () => {
    loadFrame(windowsFrame([['t.a', 't.b']]), 'tb');
    const [a, b] = current().windows[0].area.tabs.map((t) => t.instanceId);
    focus(current().windows[0].id);
    expect(entry(a)!.state).toBe('focused');
    expect(entry(b)!.state).toBe('hidden');
  });

  it('marks an unfocused window on screen as open', () => {
    focus(current().windows[0].id);
    expect(entry('t.tool#4')!.state).toBe('open');
  });

  it('marks a minimized window minimized — the only way back to it', () => {
    layoutStore.dispatch({
      type: 'SET_WINDOW_MODE',
      windowId: current().windows[0].id,
      mode: 'minimized',
    });
    expect(entry('t.a#1')!.state).toBe('minimized');
  });
});

describe('order', () => {
  it('is stable under focus, minimize and z-order changes', () => {
    openPane('t.b');
    const before = taskbarEntries(current()).map((e) => e.instanceId);
    layoutStore.dispatch({
      type: 'SET_WINDOW_MODE',
      windowId: current().windows[0].id,
      mode: 'minimized',
    });
    layoutStore.dispatch({ type: 'BRING_WINDOW_FRONT', windowId: current().windows[1].id });
    // A taskbar that reorders on focus moves the button out from under a pointer
    // already travelling toward it.
    expect(taskbarEntries(current()).map((e) => e.instanceId)).toEqual(before);
  });

  it('sorts #10 after #2 rather than lexically', () => {
    const ids: string[] = [];
    for (let i = 0; i < 10; i++) ids.push(openPane('t.b', { instanceId: `t.b#${i + 20}` })!);
    const order = taskbarEntries(current())
      .map((e) => e.instanceId)
      .filter((id) => id.startsWith('t.b#'));
    expect(order).toEqual(ids);
  });
});

describe('activate', () => {
  it('minimizes the window you are already looking at', () => {
    focus(current().windows[0].id);
    expect(entry('t.a#1')!.state).toBe('focused');
    activateTaskbarEntry('t.a#1');
    expect(entry('t.a#1')!.state).toBe('minimized');
  });

  it('restores and focuses a minimized one', () => {
    focus(current().windows[0].id);
    activateTaskbarEntry('t.a#1');
    activateTaskbarEntry('t.a#1');
    expect(entry('t.a#1')!.state).toBe('focused');
  });

  it('reveals a background tab instead of hiding anything', () => {
    loadFrame(windowsFrame([['t.a', 't.b']]), 'tb');
    focus(current().windows[0].id);
    const b = current().windows[0].area.tabs[1].instanceId;
    expect(entry(b)!.state).toBe('hidden');
    activateTaskbarEntry(b);
    expect(entry(b)!.state).toBe('focused');
  });

  it('focuses an unfocused window rather than minimizing it', () => {
    focus(current().windows[0].id);
    activateTaskbarEntry('t.tool#4');
    expect(entry('t.tool#4')!.state).toBe('focused');
  });

  it('returns false for an unknown instance rather than throwing', () => {
    expect(activateTaskbarEntry('nope#9')).toBe(false);
  });
});

describe('minimizePane', () => {
  it('minimizes from any state, unlike the click toggle', () => {
    loadFrame(windowsFrame([['t.a', 't.b']]), 'tb');
    focus(current().windows[0].id);
    const b = current().windows[0].area.tabs[1].instanceId;
    // `b` is a background tab — a click would REVEAL it, but the double-click
    // gesture and the menu item both mean "put it away" regardless.
    expect(entry(b)!.state).toBe('hidden');
    minimizePane(b);
    expect(current().windows[0].mode).toBe('minimized');
  });

  it('returns false for an unknown pane', () => {
    expect(minimizePane('nope#9')).toBe(false);
  });
});
