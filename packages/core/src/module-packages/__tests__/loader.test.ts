/**
 * The module-package loader: registers enabled web halves in dependency order,
 * and follows the backend's enable state live.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(),
}));
vi.mock('../../ws', () => ({ subscribeChannel: vi.fn(() => () => {}) }));

import { apiGet } from '../../api';
import { registry } from '../../registry';
import { loadOrder } from '../load-order';
import {
  loadModulePackages,
  moduleLoadErrors,
  resetModuleLoaderForTests,
  type ModuleEntry,
} from '../loader';
import { getModulesSnapshot, resetModulesForTests, type ModuleState } from '../store';

function entry(
  id: string,
  extra: Partial<ModuleEntry['manifest']> = {},
  order?: string[],
): ModuleEntry {
  return {
    manifest: { id, name: id, version: '1', description: '', author: '', sdkVersion: 4, ...extra },
    load: async () => {
      order?.push(id);
      return { module: { id, title: id } };
    },
  };
}

function state(id: string, enabled: boolean): ModuleState {
  return {
    id,
    name: id,
    version: '1',
    description: '',
    author: '',
    sdkVersion: 4,
    builtin: true,
    source: 'builtin',
    enabled,
    loaded: enabled,
    error: null,
  };
}

beforeEach(() => {
  registry.resetForTests();
  resetModuleLoaderForTests();
  resetModulesForTests();
});

afterEach(() => {
  vi.mocked(apiGet).mockReset();
});

describe('loadOrder', () => {
  it('puts providers and dependencies first, and survives cycles', () => {
    const { order } = loadOrder([
      { id: 'karaoke', requires: ['audio.mediaStrip@1'] },
      { id: 'audio', provides: ['audio.mediaStrip@1'] },
    ]);
    expect(order).toEqual(['audio', 'karaoke']);
    const cyc = loadOrder([
      { id: 'a', dependsOn: { b: '*' } },
      { id: 'b', dependsOn: { a: '*' } },
    ]);
    expect(cyc.order.sort()).toEqual(['a', 'b']);
    expect(cyc.problems[0]).toMatch(/cycle/);
  });
});

describe('loadModulePackages', () => {
  it('registers enabled modules in dependency order and skips disabled ones', async () => {
    vi.mocked(apiGet).mockResolvedValue({
      modules: [state('karaoke', true), state('audio', true), state('off', false)],
    });
    const order: string[] = [];
    await loadModulePackages([
      entry('karaoke', { requires: ['audio.mediaStrip@1'] }, order),
      entry('audio', { provides: ['audio.mediaStrip@1'] }, order),
      entry('off', {}, order),
    ]);
    expect(order).toEqual(['audio', 'karaoke']);
    expect(registry.has('off')).toBe(false);
    expect(getModulesSnapshot().ready).toBe(true);
  });

  it('skips an enabled module whose backend half is running in another process', async () => {
    // karaoke is enabled, but `horrible run karaoke` holds it: this backend has no
    // karaoke routes, so registering its panes would only show 404s.
    const held = {
      ...state('karaoke', true),
      loaded: false,
      error: 'karaoke is running in karaoke (pid 4242); it loads here when that stops',
    };
    vi.mocked(apiGet).mockResolvedValue({ modules: [held] });
    await loadModulePackages([
      entry('karaoke', { py: { root: 'py/src', package: 'k', entry: 'k:M' } }),
    ]);
    expect(registry.has('karaoke')).toBe(false);

    // The runner stops; the backend loads it and says so. The panes come back.
    resetModulesForTests({ ready: true, modules: [{ ...held, loaded: true, error: null }] });
    await vi.waitFor(() => expect(registry.has('karaoke')).toBe(true));
  });

  it('loads every built-in when the backend is unreachable', async () => {
    vi.mocked(apiGet).mockRejectedValue(new Error('offline'));
    await loadModulePackages([entry('a'), entry('b')]);
    expect(registry.has('a') && registry.has('b')).toBe(true);
  });

  it('follows the store: a disable unregisters, an enable registers again', async () => {
    vi.mocked(apiGet).mockResolvedValue({ modules: [state('k', true)] });
    await loadModulePackages([entry('k')]);
    expect(registry.has('k')).toBe(true);

    resetModulesForTests({ ready: true, modules: [state('k', false)] });
    await vi.waitFor(() => expect(registry.has('k')).toBe(false));

    resetModulesForTests({ ready: true, modules: [state('k', true)] });
    await vi.waitFor(() => expect(registry.has('k')).toBe(true));
  });

  it('records an entry whose id does not match its manifest', async () => {
    vi.mocked(apiGet).mockResolvedValue({ modules: [] });
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    await loadModulePackages([
      {
        manifest: entry('right').manifest,
        load: async () => ({ module: { id: 'wrong', title: '' } }),
      },
    ]);
    err.mockRestore();
    expect(registry.has('wrong')).toBe(false);
    expect(moduleLoadErrors[0]?.message).toMatch(/declares id "wrong"/);
  });
});
