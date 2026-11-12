/**
 * Vite plugin: `virtual:horrible-modules`, the app's list of built-in module packages.
 *
 * At build (and dev-server) time it reads every `modules/<id>/horrible-module.json`
 * under `root` and emits
 *
 *   export const modules = [
 *     { manifest: {…}, load: () => import('/abs/modules/karaoke/web/src/index.ts') },
 *     …
 *   ];
 *
 * Each `load` is a real dynamic import of the module's source, so the bundler
 * code-splits it and type-checks it like any other file — no Blob URLs, which stay
 * the path for third-party bundles only. A module directory checked out as a git
 * submodule is found the same way: discovery is by manifest, not by a list.
 *
 * Usage (apps/web/vite.config.ts):
 *
 *   import { horribleModules } from '@horribledashboard/sdk/vite-modules';
 *   plugins: [react(), horribleModules({ root: resolve(__dirname, '../../modules') })]
 *
 * and in the app: `import { modules } from 'virtual:horrible-modules'`.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import type { ModulePackageManifest } from './module.js';

const VIRTUAL_ID = 'virtual:horrible-modules';
const RESOLVED_ID = `\0${VIRTUAL_ID}`;

export interface HorribleModulesOptions {
  /** The directory holding module packages (`<repo>/modules`). */
  root: string;
  /** Module ids to leave out (e.g. a standalone shell bundling one module). */
  exclude?: string[];
  /** Only these module ids (and nothing else). */
  only?: string[];
}

/** Structurally a Vite `Plugin` — no vite dependency needed in the SDK. */
export interface HorribleModulesPlugin {
  name: string;
  resolveId(id: string): string | undefined;
  load(id: string): string | undefined;
  configureServer?(server: { watcher: { add(path: string): void } }): void;
}

/** Built-in module packages with a web half, in directory order. */
export function scanModulePackages(
  root: string,
): Array<{ dir: string; manifest: ModulePackageManifest }> {
  if (!existsSync(root)) return [];
  const out: Array<{ dir: string; manifest: ModulePackageManifest }> = [];
  for (const name of readdirSync(root).sort()) {
    const dir = join(root, name);
    const file = join(dir, 'horrible-module.json');
    if (!existsSync(file)) continue;
    const manifest = JSON.parse(readFileSync(file, 'utf8')) as ModulePackageManifest;
    if (manifest.id !== name) {
      throw new Error(`modules/${name}: id "${manifest.id}" must equal its directory name`);
    }
    if (manifest.web) out.push({ dir, manifest });
  }
  return out;
}

export function horribleModules(opts: HorribleModulesOptions): HorribleModulesPlugin {
  const root = resolve(opts.root);
  return {
    name: 'horrible-modules',
    resolveId(id) {
      return id === VIRTUAL_ID ? RESOLVED_ID : undefined;
    },
    load(id) {
      if (id !== RESOLVED_ID) return undefined;
      const rows = scanModulePackages(root)
        .filter(({ manifest }) => !opts.exclude?.includes(manifest.id))
        .filter(({ manifest }) => !opts.only || opts.only.includes(manifest.id))
        .map(({ dir, manifest }) => {
          // `web.entry` is relative to the package root (`web/src/index.ts`).
          const entry = resolve(dir, manifest.web!.entry).replaceAll('\\', '/');
          return `  { manifest: ${JSON.stringify(manifest)}, load: () => import(${JSON.stringify(entry)}) },`;
        });
      return `export const modules = [\n${rows.join('\n')}\n];\n`;
    },
    configureServer(server) {
      // A new or edited manifest changes the list.
      server.watcher.add(join(root, '*', 'horrible-module.json'));
    },
  };
}
