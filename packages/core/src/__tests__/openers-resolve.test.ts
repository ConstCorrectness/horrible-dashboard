/**
 * Every `openPanel('…')` in the app names a view that exists.
 *
 * The failure this catches is the quietest one in the layout: `openPanel` →
 * `openPaneRouted` → `resolveView` is a plain registry lookup, and a miss returns
 * `null`. No throw, no toast, no console line — the button is simply inert. Three of
 * them were (`games.profile`, `games.plaza`, `games.challenges`), each pointing at a
 * pane that the games consolidation turned into a *section* of `games.lobby`. The
 * saved-layout side of that rename was handled (`RENAMED_VIEWS` in `serialize.ts`)
 * and the agent's side was handled (`VIEW_ALIASES`), but the in-app buttons were
 * not, and nothing anywhere said so.
 *
 * Source-scanned rather than run against a live registry, for the same reason
 * `front-door.test.ts` reads `packages/ui`: a call site only registers its module
 * when the app boots, so a runtime check would have to import every module in the
 * workspace — and would still only cover the ones it thought to import.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { VIEW_ALIASES } from '../layout/controller';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const REPO = join(HERE, '..', '..', '..', '..');
const ROOTS = [
  join(REPO, 'packages', 'core', 'src'),
  join(REPO, 'packages', 'ui', 'src'),
  join(REPO, 'apps', 'web', 'src'),
];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === 'dist') continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (['.ts', '.tsx'].includes(extname(path))) out.push(path);
  }
  return out;
}

const files = ROOTS.flatMap((root) => walk(root));
const sources = new Map(files.map((f) => [f, readFileSync(f, 'utf8')]));

/**
 * View ids declared next to a `component:` — i.e. anything registerable as a panel
 * or a widget. A declaration is `id: 'x'` with a `component:` before the next `id:`,
 * which is the same shape `front-door.test.ts` reads backdrops with.
 */
function declaredViewIds(): Set<string> {
  const ids = new Set<string>();
  for (const source of sources.values()) {
    for (const block of source.split(/\bid:\s*'/).slice(1)) {
      const id = block.slice(0, block.indexOf("'"));
      const untilNext = block.split(/\bid:\s*'/)[0];
      if (/\bcomponent:\s*\w/.test(untilNext)) ids.add(id);
    }
  }
  return ids;
}

/** Every `openPanel('…')` / `openPane('…')` literal, with the file it came from. */
function openerCallSites(): { id: string; file: string }[] {
  const out: { id: string; file: string }[] = [];
  for (const [file, source] of sources) {
    // Tests are allowed to open a stub view they registered themselves.
    if (file.includes('__tests__')) continue;
    for (const m of source.matchAll(/\bopenPane(?:l)?\(\s*'([^']+)'/g)) {
      out.push({ id: m[1], file });
    }
  }
  return out;
}

describe('pane openers', () => {
  const declared = declaredViewIds();

  it('finds the openers and the declarations at all', () => {
    // A guard on the guard: if either regex stops matching, every assertion below
    // passes vacuously and the test becomes decoration.
    expect(declared.size).toBeGreaterThan(50);
    expect(openerCallSites().length).toBeGreaterThan(20);
  });

  it('never names a view that neither exists nor has an alias', () => {
    const unresolved = openerCallSites()
      .filter(({ id }) => !declared.has(id) && !(id in VIEW_ALIASES))
      .map(({ id, file }) => `${id} (${file.slice(REPO.length + 1)})`);
    expect(unresolved).toEqual([]);
  });

  it('resolves every alias target to a view that exists', () => {
    // An alias pointing at a second retired id would fail exactly the same way the
    // dead openers did, one level further in.
    const broken = Object.entries(VIEW_ALIASES)
      .filter(([, target]) => target.kind === 'view' && !declared.has(target.viewId))
      .map(([alias, target]) => `${alias} → ${(target as { viewId: string }).viewId}`);
    expect(broken).toEqual([]);
  });
});
