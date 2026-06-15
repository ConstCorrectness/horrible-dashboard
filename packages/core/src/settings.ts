/**
 * User settings service. Settings are *declared* in module/plugin manifests
 * (`registry.settings`) — the schema and defaults live on the frontend — while
 * only the user's *overrides* are persisted server-side (`/api/settings`). This
 * store holds the overrides and notifies subscribers on change so widgets bound
 * to a setting re-render live. The effective value of a key is its override, or
 * the declared default when never set. See docs/modules/settings.md.
 */
import { useSyncExternalStore } from 'react';

import { apiDelete, apiGet, apiPut } from './api';
import { registry } from './registry';

export type SettingValue = string | number | boolean;

interface SettingsResponse {
  values: Record<string, SettingValue>;
}

// Overrides only. Replaced by a new reference on every change so
// useSyncExternalStore sees an update.
let overrides: Record<string, SettingValue> = {};
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function declaredDefault(key: string): SettingValue | undefined {
  return registry.settings.find((s) => s.key === key)?.default;
}

/** Effective value: the override if set, else the declared default. */
export function getSetting<T extends SettingValue>(key: string): T | undefined {
  const value = key in overrides ? overrides[key] : declaredDefault(key);
  return value as T | undefined;
}

/** True when the user has overridden the declared default for this key. */
export function isSettingOverridden(key: string): boolean {
  return key in overrides;
}

/** Optimistically update locally, then persist. */
export async function setSetting(key: string, value: SettingValue): Promise<void> {
  overrides = { ...overrides, [key]: value };
  emit();
  await apiPut(`/settings/${encodeURIComponent(key)}`, { value });
}

/** Clear the override so the key falls back to its declared default. */
export async function resetSetting(key: string): Promise<void> {
  if (!(key in overrides)) return;
  const next = { ...overrides };
  delete next[key];
  overrides = next;
  emit();
  await apiDelete(`/settings/${encodeURIComponent(key)}`);
}

/**
 * Seed the store from the backend. Called once at boot before first render so
 * widgets read correct values on mount. Backend down ⇒ defaults only.
 */
export async function loadSettings(): Promise<void> {
  try {
    const res = await apiGet<SettingsResponse>('/settings');
    overrides = res.values ?? {};
    emit();
  } catch {
    // No backend: every key resolves to its declared default.
  }
}

export const settingsStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  /** Stable reference between changes — safe for useSyncExternalStore. */
  getSnapshot(): Record<string, SettingValue> {
    return overrides;
  },
};

/** Reactive read of a setting's effective value for built-in components. */
export function useSetting<T extends SettingValue>(key: string): T | undefined {
  // Re-render on any settings change, then resolve this key (override ?? default).
  useSyncExternalStore(settingsStore.subscribe, settingsStore.getSnapshot);
  return getSetting<T>(key);
}
