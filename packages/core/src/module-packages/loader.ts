/**
 * Registers module packages' web halves and keeps them in step with the backend.
 *
 * The app hands in every built-in module package as a lazy entry (the
 * `virtual:horrible-modules` list the Vite plugin generates). At boot the enabled
 * ones register, dependencies first. After boot this follows the module store:
 * a module disabled anywhere is unregistered here — its panes close, its
 * services are withdrawn, its agent tools leave the manifest — and one enabled
 * is imported and registered, all without a page reload.
 */
import type { ModulePackageManifest } from '@horribledashboard/sdk';

import type { ModuleDefinition } from '../module-def';
import { registry } from '../registry';
import { loadOrder } from './load-order';
import { getModulesSnapshot, moduleState, refreshModules, subscribeModules } from './store';

/** One module package the app can load: its manifest and its (lazy) web half. */
export interface ModuleEntry {
  manifest: ModulePackageManifest;
  load: () => Promise<{ default?: ModuleDefinition; module?: ModuleDefinition }>;
}

export interface ModuleLoadError {
  moduleId: string;
  message: string;
}

/** Web-half load failures this session, newest last. The marketplace shows them. */
export const moduleLoadErrors: ModuleLoadError[] = [];

const entries = new Map<string, ModuleEntry>();
/** Serializes register/unregister: an enable racing a disable must not interleave. */
let queue: Promise<unknown> = Promise.resolve();
let following = false;

function serialize<T>(task: () => Promise<T>): Promise<T> {
  const next = queue.then(task, task);
  queue = next.catch(() => undefined);
  return next;
}

function recordError(moduleId: string, err: unknown): void {
  const message = err instanceof Error ? err.message : String(err);
  moduleLoadErrors.push({ moduleId, message });
  console.error(`[modules] ${moduleId}: ${message}`);
}

/** Whether a module should be live in this window right now. */
function wanted(id: string): boolean {
  const snap = getModulesSnapshot();
  // Backend unreachable or not yet answered: built-ins run (an offline boot keeps
  // its panes); the backend's word wins as soon as it arrives.
  if (!snap.ready) return true;
  const state = moduleState(id);
  // `loaded`, not just `enabled`: a module whose Python half is not running in this
  // backend (it failed, or another process on the module bus holds it) would only
  // show panes whose every request 404s. Its saved panes show the reason instead.
  // For a web-only module the backend reports `loaded` equal to `enabled`.
  return state ? state.enabled && state.loaded : true;
}

type LoadedEntry = Awaited<ReturnType<ModuleEntry['load']>>;

async function registerEntry(id: string, loading?: Promise<LoadedEntry>): Promise<void> {
  const entry = entries.get(id);
  if (!entry || registry.has(id)) return;
  try {
    const mod = await (loading ?? entry.load());
    const def = mod.default ?? mod.module;
    if (!def) throw new Error('web entry must export `module` (defineModule) or a default');
    if (def.id !== id) throw new Error(`web entry declares id "${def.id}", manifest says "${id}"`);
    registry.register(def);
  } catch (err) {
    recordError(id, err);
  }
}

async function unregisterEntry(id: string): Promise<void> {
  if (!registry.has(id)) return;
  const done = await registry.unregister(id);
  if (!done) {
    // A pane refused to close (unsaved work). The backend half is already gone, so
    // say so rather than leave a pane pointing at routes that now 404.
    recordError(id, 'disabled, but a pane with unsaved work kept it open');
  }
}

/** Bring this window in line with the store: register wanted, unregister unwanted. */
function reconcile(): Promise<void> {
  return serialize(async () => {
    const ids = [...entries.keys()];
    for (const id of ids) {
      if (!wanted(id)) await unregisterEntry(id);
    }
    const toLoad = ids.filter((id) => wanted(id) && !registry.has(id));
    const { order } = loadOrder(toLoad.map((id) => entries.get(id)!.manifest));
    // Start every web half's import at once, then register in dependency order.
    // Only registration has an order to keep; importing one module at a time made
    // boot wait for the sum of the downloads rather than the slowest. Each load is
    // called once and its promise handed on, so a failed import is recorded by
    // `registerEntry` exactly as before. The no-op catch only stops a rejection
    // being reported as unhandled before that await reaches it.
    const loading = new Map<string, Promise<LoadedEntry>>();
    for (const id of order) {
      const pending = entries.get(id)!.load();
      pending.catch(() => undefined);
      loading.set(id, pending);
    }
    for (const id of order) await registerEntry(id, loading.get(id));
  });
}

/**
 * Called once at boot with every built-in module package. Resolves when the
 * enabled ones have registered (before first render, so restored layouts find
 * their panes). Then follows the store for the rest of the session.
 */
export async function loadModulePackages(list: ModuleEntry[]): Promise<void> {
  for (const e of list) entries.set(e.manifest.id, e);
  await refreshModules();
  const { problems } = loadOrder(list.map((e) => e.manifest));
  for (const p of problems) console.warn(`[modules] load order: ${p}`);
  await reconcile();
  if (!following) {
    following = true;
    subscribeModules(() => void reconcile());
  }
}

/** Test-only. */
export function resetModuleLoaderForTests(): void {
  entries.clear();
  moduleLoadErrors.length = 0;
  queue = Promise.resolve();
  following = false;
}
