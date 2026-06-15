/**
 * The public plugin contract. A plugin is an ES module whose default export is
 * `definePlugin({ setup })`; the host calls `setup(host)` at boot and registers
 * whatever the plugin contributes. Plugins are trusted code (Obsidian-style):
 * they run unsandboxed in the app realm. See docs/architecture/plugin-sdk.md.
 */
import type {
  Capability,
  CommandDecl,
  KeybindingDecl,
  PanelDecl,
  SettingDecl,
  WidgetDecl,
  WsMessage,
} from './types.js';

/**
 * Bumped on breaking changes to the plugin contract. A plugin package declares
 * the version it was built against in `horrible-plugin.json` (`sdkVersion`);
 * the loader skips plugins whose version doesn't match.
 */
export const SDK_API_VERSION = 1;

/** The `horrible-plugin.json` package manifest, as served by the backend. */
export interface PluginPackageManifest {
  /** Lowercase kebab-case, `^[a-z0-9][a-z0-9-]{0,63}$`; also the directory name. */
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  /** Entry module path relative to the package root, e.g. `dist/index.js`. */
  entry: string;
  sdkVersion: number;
  requiredCapabilities: Capability[];
  /** Informational in v1 (shown in the marketplace); enforced in a later version. */
  permissions: string[];
}

/** Key-value storage scoped to one plugin, persisted server-side. */
export interface PluginStorage {
  /** Resolves to `undefined` when the key has never been set. */
  get<T>(key: string): Promise<T | undefined>;
  set(key: string, value: unknown): Promise<void>;
  remove(key: string): Promise<void>;
}

/**
 * Read/write access to the values of settings the plugin declared (see
 * `PluginContributions.settings`). Distinct from `PluginStorage`: settings are
 * user-configurable from the settings page, storage is the plugin's own
 * bookkeeping. Reads are synchronous against an in-memory store the host keeps
 * warm; `subscribe` fires on any settings change so a widget can re-render.
 * Keys must be namespaced under the plugin id, like contributed ids.
 */
export interface PluginSettings {
  /** Current value, or the declared default if never overridden. */
  get<T>(key: string): T | undefined;
  set(key: string, value: string | number | boolean): Promise<void>;
  subscribe(listener: () => void): () => void;
}

/** Everything the host hands a plugin. The only door back into the shell. */
export interface PluginHost {
  readonly pluginId: string;
  /** Backend HTTP client, relative to `/api` (same client built-in modules use). */
  api: {
    get<T>(path: string): Promise<T>;
    post<T>(path: string, body: unknown): Promise<T>;
    put<T>(path: string, body: unknown): Promise<T>;
    del<T>(path: string): Promise<T>;
  };
  storage: PluginStorage;
  /** Values of the settings this plugin declared in its contributions. */
  settings: PluginSettings;
  hasCapability(capability: Capability): boolean;
  /** Subscribe to a channel on the shared `/ws` socket; returns unsubscribe. */
  subscribeChannel(channel: string, handler: (msg: WsMessage) => void): () => void;
  openPanel(panelId: string): void;
  runCommand(commandId: string): Promise<void>;
}

/**
 * What a plugin contributes to the shell. Every id must be namespaced under
 * the plugin id (`<pluginId>.<name>`) — the loader rejects anything else.
 */
export interface PluginContributions {
  commands?: CommandDecl[];
  panels?: PanelDecl[];
  widgets?: WidgetDecl[];
  keybindings?: KeybindingDecl[];
  /** User-configurable settings shown on the settings page (keys namespaced). */
  settings?: SettingDecl[];
}

export interface HorriblePlugin {
  setup(host: PluginHost): PluginContributions | Promise<PluginContributions>;
}

/** Identity helper that gives plugin entry modules a typed default export. */
export function definePlugin(plugin: HorriblePlugin): HorriblePlugin {
  return plugin;
}
