/**
 * The module package contract: one `horrible-module.json` describing a
 * self-contained feature module — its web half, its Python half, what it
 * depends on, which service contracts it provides and requires, which agent
 * tool groups it owns, and how it runs on its own.
 *
 * It is a superset of the older `horrible-plugin.json` (`PluginPackageManifest`).
 * `normalizeManifest` reads either shape, so a v3 plugin keeps loading unchanged.
 * See docs/architecture/modules.mdx.
 */
import type { PluginPackageManifest } from './plugin.js';
import type { Capability } from './types.js';

/** A module id: lowercase kebab-case, also its directory name under `modules/`. */
export const MODULE_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

/** An agent tool group a module owns. Every tool it registers is `<group>.<name>`. */
export interface ModuleToolGroup {
  id: string;
  /** One line the agent reads in `list_tool_groups` to decide whether to load it. */
  description: string;
  /**
   * Part of every turn's always-on tool set. Almost never right: small models stop
   * reasoning past ~40 tool definitions, so groups load on demand by default.
   */
  alwaysOn?: boolean;
}

/** How the module runs on its own (`horrible run <id>`). */
export interface ModuleStandalone {
  /** The view the standalone shell renders full-window. */
  pane: string;
  /** Preferred port; 0 or absent picks a free one. */
  port?: number;
  /** Window title; defaults to the module name. */
  title?: string;
}

export interface ModulePackageManifest {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  sdkVersion: number;
  /**
   * The browser half. `entry` is its source entry relative to the package root
   * (`web/src/index.ts`; built-ins are bundled from source); `dist` is the
   * single-file bundle a third-party module ships for the Blob-URL loader.
   */
  web?: { entry: string; dist?: string };
  /**
   * The Python half. `root` is the directory added to `sys.path` when the package
   * is not already importable; `entry` is `"<package>:<attribute>"`.
   */
  py?: { root: string; package: string; entry: string };
  /** Other modules this one needs loaded first, with semver ranges. */
  dependsOn?: Record<string, string>;
  /** Service contract ids this module provides, e.g. `"audio.mediaStrip@1"`. */
  provides?: string[];
  /** Service contract ids this module uses. Load order follows provider → user. */
  requires?: string[];
  toolGroups?: ModuleToolGroup[];
  /** `/ws` channels this module owns (and that bus peers may subscribe to). */
  wsChannels?: string[];
  /** View ids this module contributes; a disabled module's saved panes show a placeholder. */
  panes?: string[];
  /** Retired view id → its replacement, folded into the layout alias table. */
  renamedPanes?: Record<string, string>;
  standalone?: ModuleStandalone;
  requiredCapabilities?: Capability[];
  /** Informational for now (shown in the marketplace). */
  permissions?: string[];
  /** Set by discovery for modules that ship with the app; never by authors. */
  builtin?: boolean;
}

/** Either manifest shape the loaders accept. */
export type AnyPackageManifest = ModulePackageManifest | PluginPackageManifest;

function isLegacyPlugin(m: AnyPackageManifest): m is PluginPackageManifest {
  return typeof (m as PluginPackageManifest).entry === 'string' && !('web' in m);
}

/**
 * Read a `horrible-plugin.json` or a `horrible-module.json` into the one shape.
 * A legacy plugin's `entry` becomes `web.dist`: it is a prebuilt bundle.
 */
export function normalizeManifest(m: AnyPackageManifest): ModulePackageManifest {
  if (!isLegacyPlugin(m)) return m;
  const { entry, ...rest } = m;
  return { ...rest, web: { entry, dist: entry } };
}

/** Does `toolName` belong to one of the module's declared groups? */
export function ownsTool(manifest: ModulePackageManifest, toolName: string): boolean {
  const group = toolName.split('.', 1)[0];
  return (manifest.toolGroups ?? []).some((g) => g.id === group);
}
