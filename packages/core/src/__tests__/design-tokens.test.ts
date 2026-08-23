/**
 * The design-system guard.
 *
 * **Every `var(--token)` must resolve.** 25 files referenced 16 tokens that were
 * never declared anywhere. A `var()` naming an undefined property is invalid at
 * computed-value time, so each of those declarations quietly fell through to its
 * hardcoded hex fallback — which is how LocalTrack came to render a fixed
 * GitHub-dark palette in all six themes while appearing, in the source, to be fully
 * themed. Nothing failed. Nothing warned. It simply ignored the switcher.
 *
 * That is the failure mode this test exists for: a missing token does not look like
 * a bug, it looks like a theme nobody likes. Scanning source text costs nothing and
 * catches it on the day it lands rather than a year later.
 *
 * The remedy when this fails is usually to add the name to the alias block in
 * `themes.css` — most offenders are older spellings of a token that does exist.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const REPO = join(HERE, '..', '..', '..', '..');
const THEMES = join(REPO, 'packages', 'ui', 'src', 'themes.css');

/** Source roots the rule applies to. */
const ROOTS = [
  join(REPO, 'packages', 'core', 'src'),
  join(REPO, 'packages', 'ui', 'src'),
  join(REPO, 'apps', 'web', 'src'),
];

const EXTS = ['.ts', '.tsx', '.css'];
const SKIP_DIRS = new Set(['node_modules', 'dist', '__tests__', 'fonts']);

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (EXTS.some((e) => name.endsWith(e))) out.push(full);
  }
  return out;
}

const FILES = ROOTS.flatMap((r) => walk(r));
const read = (f: string) => readFileSync(f, 'utf8');
const rel = (f: string) => relative(REPO, f).split(sep).join('/');

/**
 * Custom properties a file DEFINES.
 *
 * Three spellings, because all three are in use and missing any one of them turns
 * a legitimate local variable into a false failure:
 *   - CSS            `--x: 1px`
 *   - inline style   `'--x': value`
 *   - inline style   `['--x' as string]: value`  — the cast form React/TS needs,
 *     which is how the games board sets its per-confetti `--i`.
 */
function definitionsIn(text: string): Set<string> {
  const found = new Set<string>();
  for (const m of text.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) found.add(m[1]);
  for (const m of text.matchAll(/\[?\s*'(--[a-zA-Z0-9-]+)'(?:\s+as\s+\w+)?\s*\]?\s*:/g))
    found.add(m[1]);
  return found;
}

describe('design tokens', () => {
  it('every var(--token) resolves to a declared custom property', () => {
    const declared = new Set<string>([
      ...definitionsIn(read(THEMES)),
      // A component may declare its own local custom property. Collected
      // repo-wide rather than per file, because one file often sets what a
      // sibling stylesheet reads.
      ...FILES.flatMap((f) => [...definitionsIn(read(f))]),
    ]);

    const offences: string[] = [];
    for (const file of FILES) {
      const seen = new Set<string>();
      for (const m of read(file).matchAll(/var\(\s*(--[a-zA-Z0-9-]+)/g)) {
        const token = m[1];
        if (declared.has(token) || seen.has(token)) continue;
        seen.add(token);
        offences.push(`${rel(file)} -> var(${token})`);
      }
    }

    expect(
      offences,
      'Undefined design tokens. Add the token to packages/ui/src/themes.css (to the ' +
        'derived/alias block if it is another spelling of one that exists), or point ' +
        'the call site at a token that does. A var() naming nothing is not a fallback ' +
        '— it is an invalid declaration that silently ignores the theme switcher.',
    ).toEqual([]);
  });

  it('declares the scale tokens the primitives are built on', () => {
    // Colour was always tokenised; space, type, elevation and motion were not,
    // which is why ~1,987 inline style objects each picked their own. If one of
    // these disappears, every primitive silently falls back to a browser default.
    const themes = definitionsIn(read(THEMES));
    for (const token of [
      '--space-1',
      '--space-8',
      '--fs-micro',
      '--fs-display',
      '--tracking-display',
      '--fw-bold',
      '--elev-0',
      '--elev-2',
      '--dur-fast',
      '--ease-entrance',
      '--stagger-step',
    ]) {
      expect(themes, `themes.css must declare ${token}`).toContain(token);
    }
  });
});
