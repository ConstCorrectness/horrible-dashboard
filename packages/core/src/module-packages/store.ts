/**
 * What the backend says about module packages: which exist, which are enabled,
 * which loaded. Fetched from `/api/modules` and kept live by the `/ws` `modules`
 * channel, so a module enabled in one window (or by the agent, or by `curl`)
 * appears in every open dashboard.
 */
import { useSyncExternalStore } from 'react';

import type { ModulePackageManifest } from '@horribledashboard/sdk';

import { apiDelete, apiGet, apiPost, apiPut } from '../api';
import { subscribeChannel } from '../ws';

export interface ModuleState extends ModulePackageManifest {
  builtin: boolean;
  source: 'builtin' | 'path' | 'installed' | 'entry_point';
  enabled: boolean;
  /** Python half live in the backend (a web-only module: equals `enabled`). */
  loaded: boolean;
  /** Why it failed to load, if it did. */
  error: string | null;
}

export interface ModulesSnapshot {
  /** False until the first successful fetch: "unknown" is not "none". */
  ready: boolean;
  modules: ModuleState[];
}

let snapshot: ModulesSnapshot = { ready: false, modules: [] };
const listeners = new Set<() => void>();
let unsubscribeWs: (() => void) | null = null;

function publish(next: ModulesSnapshot): void {
  snapshot = next;
  listeners.forEach((l) => l());
}

function upsert(state: ModuleState): void {
  const rest = snapshot.modules.filter((m) => m.id !== state.id);
  publish({ ...snapshot, modules: [...rest, state] });
}

export function getModulesSnapshot(): ModulesSnapshot {
  return snapshot;
}

export function subscribeModules(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useModulePackages(): ModulesSnapshot {
  return useSyncExternalStore(subscribeModules, getModulesSnapshot);
}

/** Fetch the list, and start following the `modules` channel. Resolves false offline. */
export async function refreshModules(): Promise<boolean> {
  unsubscribeWs ??= subscribeChannel('modules', (msg) => {
    if (msg.event === 'state' && msg.data) upsert(msg.data as ModuleState);
    if (msg.event === 'removed' && msg.data) {
      const { id } = msg.data as { id: string };
      publish({ ...snapshot, modules: snapshot.modules.filter((m) => m.id !== id) });
    }
  });
  try {
    const res = await apiGet<{ modules: ModuleState[] }>('/modules');
    publish({ ready: true, modules: res.modules });
    return true;
  } catch {
    return false;
  }
}

export function moduleState(id: string): ModuleState | undefined {
  return snapshot.modules.find((m) => m.id === id);
}

/** The module package that declares `viewId` in its `panes`, if any. */
export function moduleOwningView(viewId: string): ModuleState | undefined {
  return snapshot.modules.find((m) => m.panes?.includes(viewId));
}

/** Enable or disable a module (live). Rejects with the backend's reason (e.g. dependents). */
export async function setModuleEnabled(id: string, enabled: boolean): Promise<ModuleState> {
  const state = await apiPut<ModuleState>(`/modules/${id}/enabled`, { enabled });
  upsert(state);
  return state;
}

export async function installModule(id: string): Promise<ModuleState> {
  const state = await apiPost<ModuleState>('/modules/install', { id });
  upsert(state);
  return state;
}

export async function uninstallModule(id: string): Promise<void> {
  await apiDelete(`/modules/${id}`);
  publish({ ...snapshot, modules: snapshot.modules.filter((m) => m.id !== id) });
}

/** Test-only. */
export function resetModulesForTests(next: ModulesSnapshot = { ready: false, modules: [] }): void {
  unsubscribeWs?.();
  unsubscribeWs = null;
  publish(next);
}
