/**
 * Every stylesheet in `packages/ui` is imported by something.
 *
 * A component imports the CSS it needs, so deleting a component can orphan a
 * stylesheet other components still depend on — and nothing says so. Typecheck is
 * happy, every test passes, and the rules just stop applying. That is how
 * `layout/frame.css` went: the tiling frame's `Frame.tsx` and `WorkspaceTabs.tsx`
 * were its only importers, and when they were removed `.shell--frame` lost its
 * `flex-direction: column`. The taskbar then sat *beside* the desktop instead of
 * under it, took all but one pixel of the width, and every window was clamped onto
 * a 1px-wide surface.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const UI = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'ui', 'src');

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) walk(path, out);
    else out.push(path);
  }
  return out;
}

const files = walk(UI);
const sources = files
  .filter((f) => /\.(tsx?|css)$/.test(f))
  .map((f) => ({ dir: dirname(f), text: readFileSync(f, 'utf8') }));

/** Every stylesheet a source file pulls in, as an absolute path. */
const imported = new Set(
  sources.flatMap(({ dir, text }) =>
    [...text.matchAll(/(?:import|@import)\s+['"](\.{1,2}\/[^'"]+\.css)['"]/g)].map((m) =>
      join(dir, m[1]),
    ),
  ),
);

describe('ui stylesheets', () => {
  it('are each imported by something', () => {
    const orphans = files
      .filter((f) => f.endsWith('.css') && !imported.has(f))
      .map((f) => relative(UI, f).replace(/\\/g, '/'));
    expect(orphans).toEqual([]);
  });
});
