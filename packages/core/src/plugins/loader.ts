/**
 * Boot-time plugin loader: fetches the installed list, dynamically imports
 * each enabled plugin's entry module from the backend, and registers its
 * contributions. One broken plugin never breaks boot — failures land in
 * `pluginLoadErrors`, which the marketplace panel renders.
 */
import {
  isSupportedSdkVersion,
  MIN_SDK_API_VERSION,
  SDK_API_VERSION,
  type HorriblePlugin,
  type PluginContributions,
  type PluginPackageManifest,
} from '@horribledashboard/sdk';

import { apiGet } from '../api';
import { apiUrl } from '../origin';
import { registry } from '../registry';
import { createPluginHost } from './host';
import { installPluginRuntime } from './runtime';

export interface InstalledPlugin {
  manifest: PluginPackageManifest;
  enabled: boolean;
}

export interface InstalledPluginList {
  plugins: InstalledPlugin[];
}

export interface PluginLoadError {
  pluginId: string;
  message: string;
}

/** Load failures from the current boot, in load order. */
export const pluginLoadErrors: PluginLoadError[] = [];

function assertNamespaced(pluginId: string, contributions: PluginContributions): void {
  const prefix = `${pluginId}.`;
  const ids = [
    ...(contributions.commands ?? []).map((c) => c.id),
    ...(contributions.panels ?? []).map((p) => p.id),
    ...(contributions.widgets ?? []).map((w) => w.id),
    ...(contributions.settings ?? []).map((s) => s.key),
  ];
  for (const id of ids) {
    if (!id.startsWith(prefix)) {
      throw new Error(`contributed id "${id}" must be namespaced under "${prefix}"`);
    }
  }
}

/**
 * Import a plugin entry module. The entry may live on a different origin than
 * the page (desktop: backend at 127.0.0.1:<port>, page at the Vite/app://
 * origin), but its shim imports (`/plugin-runtime/*.js`, emitted root-absolute
 * by the SDK build preset) are served by the PAGE origin. A direct import()
 * would resolve them against the entry's origin and 404 — so fetch the source,
 * pin the shim specifiers to the page origin, and import via a Blob URL.
 * Consequence: plugin bundles must be a single file (a Blob module has no base
 * URL for relative chunk imports) — documented in plugin-sdk.md.
 */
async function importEntry(entryUrl: string): Promise<{ default?: HorriblePlugin }> {
  const res = await fetch(entryUrl);
  if (!res.ok) throw new Error(`failed to fetch entry module (${res.status})`);
  const source = (await res.text()).replaceAll(
    /(["'])\/plugin-runtime\//g,
    `$1${window.location.origin}/plugin-runtime/`,
  );
  const blobUrl = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
  try {
    // Not served by Vite — skip its import analysis.
    return (await import(/* @vite-ignore */ blobUrl)) as { default?: HorriblePlugin };
  } finally {
    URL.revokeObjectURL(blobUrl);
  }
}

async function loadOne(manifest: PluginPackageManifest): Promise<void> {
  const { id } = manifest;
  try {
    if (!isSupportedSdkVersion(manifest.sdkVersion)) {
      throw new Error(
        `built for SDK v${manifest.sdkVersion}; this app accepts v${MIN_SDK_API_VERSION}–v${SDK_API_VERSION}`,
      );
    }
    const mod = await importEntry(apiUrl(`/api/plugins/${id}/assets/${manifest.entry}`));
    if (typeof mod.default?.setup !== 'function') {
      throw new Error('entry module must default-export definePlugin({ setup })');
    }
    const contributions = await mod.default.setup(createPluginHost(id));
    assertNamespaced(id, contributions);
    // `plugin:` prefix keeps plugin module ids from shadowing built-in modules.
    registry.register({ id: `plugin:${id}`, title: manifest.name, ...contributions });
  } catch (err) {
    pluginLoadErrors.push({
      pluginId: id,
      message: err instanceof Error ? err.message : String(err),
    });
  }
}

/**
 * Called once by the app entry after built-in modules register, before first
 * render (so restored layouts find plugin panels/widgets). Backend down means
 * a normal, pluginless boot.
 */
export async function loadPlugins(): Promise<void> {
  installPluginRuntime();
  let installed: InstalledPluginList;
  try {
    installed = await apiGet<InstalledPluginList>('/plugins/installed');
  } catch {
    return;
  }
  await Promise.allSettled(
    installed.plugins.filter((p) => p.enabled).map((p) => loadOne(p.manifest)),
  );
}

/**
 * Load one plugin into the running app — after an install or enable — with no
 * page reload. Same path as boot; a failure lands in `pluginLoadErrors`.
 */
export async function loadPlugin(manifest: PluginPackageManifest): Promise<boolean> {
  installPluginRuntime();
  const before = pluginLoadErrors.length;
  // A previous failure for this plugin is stale once we try again.
  for (let i = pluginLoadErrors.length - 1; i >= 0; i--) {
    if (pluginLoadErrors[i].pluginId === manifest.id) pluginLoadErrors.splice(i, 1);
  }
  await loadOne(manifest);
  return pluginLoadErrors.length <= before && registry.has(`plugin:${manifest.id}`);
}

/**
 * Take one plugin out of the running app — after a disable or uninstall. Its open
 * panes close through the guarded path; resolves false if one refused (unsaved
 * work), in which case the plugin stays registered.
 */
export function unloadPlugin(pluginId: string): Promise<boolean> {
  return registry.unregister(`plugin:${pluginId}`);
}
