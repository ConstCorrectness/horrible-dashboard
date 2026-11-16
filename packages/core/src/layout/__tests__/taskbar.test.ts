/**
 * Pane-switcher derivation: one entry per open pane, the four states, the stable
 * order, and what a click resolves to.
 *
 * Views are registered synthetically: importing a real module manifest reaches
 * the editor's module-scope WebSocket and dies without jsdom.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { activateTaskbarEntry, openPane, toggleDock } from '../controller';
import { layoutStore } from '../store';
import { taskbarEntries } from '../taskbar';
import { current, groupsFrame, loadFrame } from './fixtures';

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
  // `t.a#4` and `t.b#5` tabbed in the centre (a6); `t.tool#7` in the right dock.
  loadFrame(groupsFrame([['t.a', 't.b']], { right: 't.tool' }), 'tb');
});

const entry = (instanceId: string) =>
  taskbarEntries(current()).find((e) => e.instanceId === instanceId);
const focus = (instanceId: string) => layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId });

describe('membership', () => {
  it('lists every open pane once, tabs separately', () => {
    expect(taskbarEntries(current()).map((e) => e.viewId)).toEqual(['t.a', 't.b', 't.tool']);
  });

  it('drops a pane whose view is no longer registered rather than showing a blank', () => {
    loadFrame(groupsFrame(['not.registered']), 'tb');
    expect(taskbarEntries(current())).toHaveLength(0);
  });
});

describe('states', () => {
  it('marks the focused pane focused and a background tab hidden', () => {
    focus('t.a#4');
    expect(entry('t.a#4')!.state).toBe('focused');
    expect(entry('t.b#5')!.state).toBe('hidden');
  });

  it('marks an unfocused pane on screen as open', () => {
    focus('t.a#4');
    expect(entry('t.tool#7')!.state).toBe('open');
  });

  it('marks a pane in a hidden dock minimized', () => {
    toggleDock('right', false);
    expect(entry('t.tool#7')!.state).toBe('minimized');
  });

  it('marks a pane behind a maximized group hidden', () => {
    layoutStore.dispatch({ type: 'TOGGLE_MAXIMIZED', areaId: 'a6' });
    expect(entry('t.tool#7')!.state).toBe('hidden');
  });
});

describe('order', () => {
  it('is stable under focus and dock changes', () => {
    openPane('t.b');
    const before = taskbarEntries(current()).map((e) => e.instanceId);
    toggleDock('right', false);
    focus('t.b#5');
    expect(taskbarEntries(current()).map((e) => e.instanceId)).toEqual(before);
  });

  it('sorts #10 after #2 rather than lexically', () => {
    const ids: string[] = [];
    for (let i = 0; i < 10; i++) ids.push(openPane('t.b', { instanceId: `t.b#${i + 20}` })!);
    const order = taskbarEntries(current())
      .map((e) => e.instanceId)
      .filter((id) => id.startsWith('t.b#') && id !== 't.b#5');
    expect(order).toEqual(ids);
  });
});

describe('activate', () => {
  it('puts away the dock you are already looking at', () => {
    focus('t.tool#7');
    activateTaskbarEntry('t.tool#7');
    expect(entry('t.tool#7')!.state).toBe('minimized');
  });

  it('shows and focuses a pane in a hidden dock', () => {
    toggleDock('right', false);
    activateTaskbarEntry('t.tool#7');
    expect(entry('t.tool#7')!.state).toBe('focused');
    expect(current().docks.right.visible).toBe(true);
  });

  it('reveals a background tab', () => {
    focus('t.a#4');
    activateTaskbarEntry('t.b#5');
    expect(entry('t.b#5')!.state).toBe('focused');
  });

  it('leaves a focused centre pane where it is', () => {
    focus('t.a#4');
    activateTaskbarEntry('t.a#4');
    expect(entry('t.a#4')!.state).toBe('focused');
  });

  it('returns false for an unknown instance rather than throwing', () => {
    expect(activateTaskbarEntry('nope#9')).toBe(false);
  });
});
