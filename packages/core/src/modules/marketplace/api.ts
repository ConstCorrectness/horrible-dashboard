/** Typed client for the backend plugins module (`/api/plugins/*`). */
import type { PluginPackageManifest } from '@horrible/sdk';

import { apiDelete, apiGet, apiPost, apiPut } from '../../api';
import type { InstalledPlugin, InstalledPluginList } from '../../plugins/loader';

export interface PluginCatalog {
  plugins: PluginPackageManifest[];
}

export function getPluginCatalog(): Promise<PluginCatalog> {
  return apiGet<PluginCatalog>('/plugins/catalog');
}

export function getInstalledPlugins(): Promise<InstalledPluginList> {
  return apiGet<InstalledPluginList>('/plugins/installed');
}

export function installPlugin(id: string): Promise<InstalledPlugin> {
  return apiPost<InstalledPlugin>('/plugins/install', { id });
}

export function uninstallPlugin(id: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(`/plugins/${id}`);
}

export function setPluginEnabled(id: string, enabled: boolean): Promise<InstalledPlugin> {
  return apiPut<InstalledPlugin>(`/plugins/${id}/enabled`, { enabled });
}
