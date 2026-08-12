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
  /**
   * Secret-shaped keys that have a value stored. The values themselves are never
   * served — `GET /api/settings` hands the whole bag to whatever asked, plugins
   * included — so all the frontend can know about a token is *that* it is set.
   */
  secretKeys?: string[];
}

// Overrides only. Replaced by a new reference on every change so
// useSyncExternalStore sees an update.
let overrides: Record<string, SettingValue> = {};
const secretKeys = new Set<string>();
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

/**
 * True when the backend refuses to serve this key's value (a token, key or
 * secret). Such a setting is **write-only from here**: `getSetting` returns the
 * blank the server sent, not the stored credential, so a control bound to one
 * must render a placeholder rather than treating empty as "not set".
 */
export function isSecretSetting(key: string): boolean {
  return SECRET_SUFFIXES.some((suffix) => key.toLowerCase().endsWith(suffix));
}

/** True when a secret setting has a value stored server-side. */
export function isSecretSet(key: string): boolean {
  return secretKeys.has(key);
}

/** Mirrors `SECRET_KEY_SUFFIXES` in backend/modules/settings/models.py. The two
 * lists disagreeing is not a crash: the backend's is the one that redacts, and
 * this one only decides whether the control renders as a password field. */
const SECRET_SUFFIXES = [
  '.key',
  '.token',
  '.secret',
  '.password',
  '.apikey',
  'secret',
  'token',
  'password',
];

/** Optimistically update locally, then persist. */
export async function setSetting(key: string, value: SettingValue): Promise<void> {
  overrides = { ...overrides, [key]: value };
  if (isSecretSetting(key)) {
    // The value is never read back, so "is it set" is tracked here instead.
    // Mutated in place: `overrides` is the store's snapshot and is replaced on
    // the line above, so subscribers already re-render — this set only needs to
    // be correct by the time they read it.
    if (value === '' || value === null) secretKeys.delete(key);
    else secretKeys.add(key);
  }
  emit();
  await apiPut(`/settings/${encodeURIComponent(key)}`, { value });
}

/** Clear the override so the key falls back to its declared default. */
export async function resetSetting(key: string): Promise<void> {
  if (!(key in overrides)) return;
  const next = { ...overrides };
  delete next[key];
  overrides = next;
  secretKeys.delete(key);
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
    secretKeys.clear();
    for (const key of res.secretKeys ?? []) secretKeys.add(key);
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
