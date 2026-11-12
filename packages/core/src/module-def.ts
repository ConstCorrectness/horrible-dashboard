/**
 * `defineModule`: what a module package's web half exports.
 *
 * A module is its `ModuleManifest` (panes, commands, settings …) plus an optional
 * `activate`, run when the module loads. `activate` is where a module does the
 * things a static manifest can't: provide a service contract, subscribe to a
 * channel, start a store. Everything it starts it hands to `ctx.onDispose`, so
 * an unload — the user disabling the module, or uninstalling it — tears it all
 * down without a page reload.
 *
 * See docs/architecture/modules.mdx.
 */
import type { ServiceContracts, ServiceKey } from '@horribledashboard/sdk';

import type { ModuleManifest } from './registry';

export interface ModuleContext {
  readonly moduleId: string;
  /** Provide a service contract; withdrawn automatically when the module unloads. */
  provide<K extends ServiceKey>(key: K, impl: ServiceContracts[K]): void;
  /** A service another module provides, or `undefined` while none does. */
  getService<K extends ServiceKey>(key: K): ServiceContracts[K] | undefined;
  /** Run `dispose` when the module unloads. Disposers run in reverse order. */
  onDispose(dispose: () => void): void;
}

export interface ModuleDefinition extends ModuleManifest {
  activate?(ctx: ModuleContext): void | Promise<void>;
}

/** Identity helper that types a module package's export. */
export function defineModule(def: ModuleDefinition): ModuleDefinition {
  return def;
}
