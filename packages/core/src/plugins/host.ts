/** Builds the `PluginHost` handle passed to each plugin's `setup()`. */
import type { PluginHost, PluginSettings, PluginStorage } from '@horribledashboard/sdk';

import { ApiError, apiDelete, apiGet, apiPost, apiPut } from '../api';
import { hasCapability } from '../capabilities';
import { registry } from '../registry';
import { getSetting, setSetting, settingsStore, type SettingValue } from '../settings';
import { subscribeChannel } from '../ws';

interface StorageEntry {
  key: string;
  value: unknown;
}

function createStorage(pluginId: string): PluginStorage {
  const base = `/plugins/${pluginId}/storage`;
  return {
    async get<T>(key: string): Promise<T | undefined> {
      try {
        const entry = await apiGet<StorageEntry>(`${base}/${encodeURIComponent(key)}`);
        return entry.value as T;
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return undefined;
        throw err;
      }
    },
    async set(key: string, value: unknown): Promise<void> {
      await apiPut(`${base}/${encodeURIComponent(key)}`, { value });
    },
    async remove(key: string): Promise<void> {
      await apiDelete(`${base}/${encodeURIComponent(key)}`);
    },
  };
}

function createSettings(pluginId: string): PluginSettings {
  // A plugin may only touch settings it declared — i.e. keys under its own
  // namespace, the same rule the loader enforces on contributed ids.
  const requireOwn = (key: string): void => {
    if (!key.startsWith(`${pluginId}.`)) {
      throw new Error(`setting "${key}" is not namespaced under "${pluginId}."`);
    }
  };
  return {
    get<T>(key: string): T | undefined {
      requireOwn(key);
      return getSetting<SettingValue>(key) as T | undefined;
    },
    set(key: string, value: SettingValue): Promise<void> {
      requireOwn(key);
      return setSetting(key, value);
    },
    subscribe: settingsStore.subscribe,
  };
}

export function createPluginHost(pluginId: string): PluginHost {
  return {
    pluginId,
    api: {
      get: apiGet,
      post: apiPost,
      put: apiPut,
      del: apiDelete,
    },
    storage: createStorage(pluginId),
    settings: createSettings(pluginId),
    hasCapability,
    subscribeChannel,
    openPanel: (panelId) => registry.openPanel(panelId),
    runCommand: (commandId) => registry.runCommand(commandId),
  };
}
