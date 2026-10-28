/**
 * Every docs page the titlebar's Help item can open exists.
 *
 * `packages/ui/src/desktop/help-links.ts` maps a module id to its page in
 * `docs/`. A wrong entry fails silently in the worst way: the item opens the
 * docs site's 404, and the user concludes the pane has no documentation. So each
 * listed page is checked against the file it will be built from.
 *
 * Read as text rather than imported: core tests do not import from `packages/ui`
 * (that direction is a dependency cycle), and the two literals this needs are
 * plain enough to pull out with a regex.
 */
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const REPO = join(HERE, '..', '..', '..', '..');
const SOURCE = readFileSync(join(REPO, 'packages/ui/src/desktop/help-links.ts'), 'utf8');

function block(name: string): string {
  const m = new RegExp(`export const ${name}[^=]*=\\s*(?:new Set\\(\\[)?([\\s\\S]*?)[\\]}]`).exec(
    SOURCE,
  );
  if (!m) throw new Error(`${name} not found in help-links.ts`);
  return m[1]!;
}

const pages = [...block('MODULE_PAGES').matchAll(/'([^']+)'/g)].map((m) => `modules/${m[1]}`);
const aliases = [...block('PAGE_ALIASES').matchAll(/:\s*'([^']+)'/g)].map((m) => m[1]!);

describe('help links', () => {
  it('found the tables', () => {
    expect(pages.length).toBeGreaterThan(40);
    expect(aliases).toContain('architecture/desktop-shell');
  });

  it.each([...pages, ...aliases])('docs/%s.mdx exists', (page) => {
    expect(existsSync(join(REPO, 'docs', `${page}.mdx`))).toBe(true);
  });
});
