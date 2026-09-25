/**
 * Every built-in module declares a launcher category.
 *
 * `registry.viewCategory` falls back to `plugins` for a view whose module names
 * none — correct for a third-party plugin, and silently wrong for a built-in one,
 * whose panes would be filed in the Start menu under "Plugins" with nothing to
 * say why. Source-scanned like `openers-resolve.test.ts`: a module only registers
 * when the app boots, so a runtime check would cover only what it thought to import.
 */
import { describe, expect, it } from 'vitest';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { PANE_CATEGORY_LABELS } from '../registry';

const MODULES = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'modules');

const manifests = readdirSync(MODULES)
  .map((m) => ['index.ts', 'index.tsx'].map((f) => join(MODULES, m, f)).find(existsSync))
  .filter((f): f is string => Boolean(f))
  .map((f) => ({ file: f, src: readFileSync(f, 'utf8') }))
  .filter(({ src }) => /: ModuleManifest = \{/.test(src));

describe('start menu categories', () => {
  it('finds the module manifests', () => {
    expect(manifests.length).toBeGreaterThan(40);
  });

  it.each(manifests.map((m) => [m.file, m.src]))('%s declares a category', (_file, src) => {
    const match =
      /: ModuleManifest = \{\r?\n\s+id: [^\r\n]+\r?\n\s+title: [^\r\n]+\r?\n\s+category: '(\w+)'/.exec(
        src,
      );
    expect(match, 'category must follow id/title in the manifest').not.toBeNull();
    expect(Object.keys(PANE_CATEGORY_LABELS)).toContain(match![1]);
    expect(match![1]).not.toBe('plugins');
  });
});
