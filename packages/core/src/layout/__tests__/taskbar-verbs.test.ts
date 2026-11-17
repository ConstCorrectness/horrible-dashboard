/**
 * Hiding a dock and closing a pane are on opposite sides of "unmount is not close".
 *
 * Close is the one verb that must dispose the pane's long-lived resources; hiding
 * must never touch them. With a live room, a terminal or a browser engine behind
 * the pane, getting this backwards either kills an Agora/PubNub session because
 * someone put a panel away, or leaks one after they closed it.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { registry } from '../../registry';
import { closePaneGuarded, openPane, toggleDock } from '../controller';
import { paneSession, paneSessionKey, resetPaneSessionsForTests } from '../pane-lifetime';
import { layoutStore } from '../store';
import { taskbarEntries } from '../taskbar';
import { groupsFrame, loadFrame } from './fixtures';

const Stub = () => null;

beforeAll(() => {
  registry.register({
    id: 'taskbar-verbs-test',
    title: 'Taskbar verbs test',
    panels: [
      { id: 'tv.a', title: 'Alpha', component: Stub, role: 'tool', icon: 'A' },
      { id: 'tv.b', title: 'Beta', component: Stub, role: 'tool', icon: 'B' },
    ],
  });
});

beforeEach(() => {
  resetPaneSessionsForTests();
  layoutStore.resetForTests();
  loadFrame(groupsFrame([], { bottom: ['tv.a', 'tv.b'] }), 'tv');
});

const frame = () => layoutStore.getSnapshot().frame;
const firstEntry = () => taskbarEntries(frame())[0];

/** Attach a resource to a pane the way a live room / PTY / browser engine does. */
function attach(instanceId: string) {
  const dispose = vi.fn();
  paneSession(
    paneSessionKey(layoutStore.getSnapshot().workspaceId, instanceId),
    () => ({ live: true }),
    dispose,
  );
  return dispose;
}

describe('dock verbs and pane-lifetime', () => {
  it('Close disposes the pane resource', async () => {
    const { instanceId } = firstEntry();
    const dispose = attach(instanceId);

    await closePaneGuarded(instanceId);

    expect(dispose).toHaveBeenCalledTimes(1);
    expect(taskbarEntries(frame()).some((e) => e.instanceId === instanceId)).toBe(false);
  });

  it('hiding its dock keeps the pane resource alive', () => {
    const { instanceId } = firstEntry();
    const dispose = attach(instanceId);

    expect(toggleDock('bottom', false)).toBe(true);

    // The room is still connected; only its dock went away.
    expect(dispose).not.toHaveBeenCalled();
    expect(taskbarEntries(frame()).some((e) => e.instanceId === instanceId)).toBe(true);
  });

  it('hiding keeps a background tab alive too', () => {
    /** Hiding a dock hides every tab in it — the tab that was not even showing
     *  must come through it untouched. */
    const background = taskbarEntries(frame())[1].instanceId;
    const dispose = attach(background);

    toggleDock('bottom', false);

    expect(dispose).not.toHaveBeenCalled();
  });

  it('a pane in a hidden dock can still be closed, and then disposes', async () => {
    const { instanceId } = firstEntry();
    const dispose = attach(instanceId);
    toggleDock('bottom', false);
    expect(dispose).not.toHaveBeenCalled();

    await closePaneGuarded(instanceId);
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  it('closing one pane leaves another pane resource alone', async () => {
    const { instanceId: first } = firstEntry();
    const second = openPane('tv.b', { instanceId: 'tv.b#99' })!;

    const disposeFirst = attach(first);
    const disposeSecond = attach(second);

    await closePaneGuarded(first);

    expect(disposeFirst).toHaveBeenCalledTimes(1);
    expect(disposeSecond).not.toHaveBeenCalled();
  });
});
