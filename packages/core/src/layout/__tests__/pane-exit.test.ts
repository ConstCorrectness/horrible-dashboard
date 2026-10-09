/**
 * A pane's exit plays before it is removed, and only when it really goes: after
 * its close guard has agreed, never after a veto, and never for longer than
 * `PANE_EXIT_MAX_MS` however long the animation claims to take.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { registry } from '../../registry';
import { registerCloseGuard } from '../close-guards';
import { closePaneGuarded } from '../controller';
import { listPanes } from '../model';
import { PANE_EXIT_MAX_MS, setPaneExitAnimator } from '../pane-exit';
import { resetPaneSessionsForTests } from '../pane-lifetime';
import { seedFromPreset, type FramePreset } from '../presets';
import { layoutStore } from '../store';

const Stub = () => null;
const preset: FramePreset = {
  id: 'px',
  name: 'Pane exit',
  frame: { center: { split: 'row', children: [{ pane: 'px.a' }, { tabs: [] }] } },
};

beforeAll(() => {
  registry.register({
    id: 'pane-exit-test',
    title: 'Pane exit test',
    panels: [{ id: 'px.a', title: 'Alpha', component: Stub, role: 'document', icon: 'A' }],
  });
});

let uninstall: (() => void) | null = null;

beforeEach(() => {
  resetPaneSessionsForTests();
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'px',
    frame: seedFromPreset(preset, { knownViews: new Set(['px.a']) }),
  });
});

afterEach(() => {
  uninstall?.();
  uninstall = null;
  vi.useRealTimers();
});

const panes = () => listPanes(layoutStore.getSnapshot().frame).map((p) => p.pane.instanceId);

describe('pane exit', () => {
  it('plays before the pane is removed', async () => {
    const [id] = panes();
    const seen: string[][] = [];
    uninstall = setPaneExitAnimator((instanceId) => {
      expect(instanceId).toBe(id);
      // Still in the layout while its exit plays.
      seen.push(panes());
    });
    expect(await closePaneGuarded(id)).toBe(true);
    expect(seen).toEqual([[id]]);
    expect(panes()).toEqual([]);
  });

  it('does not play when the close guard vetoes', async () => {
    const [id] = panes();
    const exit = vi.fn();
    uninstall = setPaneExitAnimator(exit);
    const unguard = registerCloseGuard(id, () => false);
    expect(await closePaneGuarded(id)).toBe(false);
    unguard();
    expect(exit).not.toHaveBeenCalled();
    expect(panes()).toEqual([id]);
  });

  it('closes anyway when the animation stalls or throws', async () => {
    vi.useFakeTimers();
    const [id] = panes();
    uninstall = setPaneExitAnimator(() => new Promise<void>(() => {}));
    const closing = closePaneGuarded(id);
    await vi.advanceTimersByTimeAsync(PANE_EXIT_MAX_MS);
    expect(await closing).toBe(true);
    expect(panes()).toEqual([]);
  });

  it('closes when the animator throws', async () => {
    const [id] = panes();
    uninstall = setPaneExitAnimator(() => {
      throw new Error('no element');
    });
    expect(await closePaneGuarded(id)).toBe(true);
  });

  it('an uninstaller removes only its own animator', async () => {
    const first = vi.fn();
    const second = vi.fn();
    const removeFirst = setPaneExitAnimator(first);
    uninstall = setPaneExitAnimator(second);
    removeFirst();
    await closePaneGuarded(panes()[0]);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
