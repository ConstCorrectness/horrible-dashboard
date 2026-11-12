/**
 * `registry.unregister`: a module leaves the running app with no reload.
 *
 * Its open panes close through the guarded path (so their pane sessions are
 * disposed — the thing unmount never does), a veto stops the whole unload,
 * `activate`'s disposers run, its services are withdrawn, and listeners fire so
 * the agent manifest is re-pushed.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ModuleDefinition } from '../../module-def';
import { registry } from '../../registry';
import { registerCloseGuard } from '../close-guards';
import { installFrameController } from '../controller';
import { listPanes } from '../model';
import {
  hasPaneSession,
  paneSession,
  paneSessionKey,
  resetPaneSessionsForTests,
} from '../pane-lifetime';
import { layoutStore } from '../store';
import { windowsFrame } from './fixtures';

const Stub = () => null;

function karaokeLike(events: string[]): ModuleDefinition {
  return {
    id: 'mu-test',
    title: 'Unregister test',
    panels: [
      {
        id: 'mu.stage',
        title: 'Stage',
        component: Stub,
        role: 'document',
        icon: 'S',
        agentTools: [
          { name: 'mu.play', description: 'play', params: { type: 'object' }, handler: () => 'ok' },
        ],
      },
    ],
    activate(ctx) {
      events.push('activate');
      ctx.provide('audio.mediaStrip@1', { useMediaStrip: () => {} });
      ctx.onDispose(() => events.push('dispose'));
    },
  };
}

beforeAll(() => {
  installFrameController();
});

beforeEach(() => {
  registry.resetForTests();
  resetPaneSessionsForTests();
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'mu',
    frame: windowsFrame(['mu.stage']),
  });
});

afterEach(() => {
  registry.resetForTests();
});

const openPanes = () => listPanes(layoutStore.getSnapshot().frame).map((p) => p.pane);

describe('registry.unregister', () => {
  it('closes the module panes, disposes their sessions, withdraws services and notifies', async () => {
    const events: string[] = [];
    registry.register(karaokeLike(events));
    expect(events).toEqual(['activate']);
    expect(registry.getService('audio.mediaStrip@1')).toBeDefined();

    const [pane] = openPanes();
    const key = paneSessionKey('mu', pane.instanceId);
    const disposed = vi.fn();
    paneSession(key, () => 'pty', disposed);

    const changed = vi.fn();
    const off = registry.onChange(changed);
    expect(await registry.unregister('mu-test')).toBe(true);
    off();

    expect(openPanes()).toEqual([]);
    expect(disposed).toHaveBeenCalledOnce();
    expect(hasPaneSession(key)).toBe(false);
    expect(events).toEqual(['activate', 'dispose']);
    expect(registry.getService('audio.mediaStrip@1')).toBeUndefined();
    expect(registry.has('mu-test')).toBe(false);
    expect(registry.panels.some((p) => p.id === 'mu.stage')).toBe(false);
    expect(changed).toHaveBeenCalled();
  });

  it('a pane that vetoes its close stops the unload and changes nothing', async () => {
    const events: string[] = [];
    registry.register(karaokeLike(events));
    const [pane] = openPanes();
    const off = registerCloseGuard(pane.instanceId, () => false);

    expect(await registry.unregister('mu-test')).toBe(false);
    off();

    expect(openPanes()).toHaveLength(1);
    expect(registry.has('mu-test')).toBe(true);
    expect(events).toEqual(['activate']);
    expect(registry.getService('audio.mediaStrip@1')).toBeDefined();
  });

  it('can register again after unregistering, re-running activate', async () => {
    const events: string[] = [];
    registry.register(karaokeLike(events));
    await registry.unregister('mu-test');
    registry.register(karaokeLike(events));
    expect(events).toEqual(['activate', 'dispose', 'activate']);
    expect(registry.getService('audio.mediaStrip@1')).toBeDefined();
  });

  it("withdrawing a service never removes a later provider's", () => {
    const first = { useMediaStrip: () => {} };
    const second = { useMediaStrip: () => {} };
    const withdrawFirst = registry.provideService('audio.mediaStrip@1', first, 'a');
    registry.provideService('audio.mediaStrip@1', second, 'b');
    withdrawFirst();
    expect(registry.getService('audio.mediaStrip@1')).toBe(second);
  });

  it('an activate that throws leaves the module registered and records why', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    registry.register({
      id: 'mu-bad',
      title: 'Bad',
      activate() {
        throw new Error('boom');
      },
    });
    err.mockRestore();
    expect(registry.has('mu-bad')).toBe(true);
    expect(registry.activationErrors.get('mu-bad')).toBe('boom');
  });
});
