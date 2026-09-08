/**
 * Every workspace preset says what it is for.
 *
 * The 17 `FramePreset`s are the most useful "here is a whole way of working"
 * affordance in the app, and each was designed with a paragraph of rationale in a
 * comment above it. None of that reached a user: the tab strip renders an icon and a
 * name, and it does not render at all on the floating desktop the app boots into
 * (`WorkspaceTabs.tsx` returns null for `mode: 'floating'`, and
 * `DEFAULT_BOOT_WORKSPACE` is `desktop`).
 *
 * `description` is what the home launcher and the welcome widget read. A preset
 * without one renders as a bare name in both — which is the state this whole surface
 * was in, so a comment asking for it would not have held.
 *
 * Source-scanned rather than read off a live registry, for the reason
 * `openers-resolve.test.ts` gives: a manifest only registers when the app boots, so a
 * runtime check would have to import every module in the workspace — and a core
 * vitest file that imports a manifest reaching the editor dies at import (a WebSocket
 * at module scope, no jsdom).
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const REPO = join(HERE, '..', '..', '..', '..');
const MODULES = join(REPO, 'packages', 'core', 'src', 'modules');

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === 'dist' || entry === '__tests__') continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (['.ts', '.tsx'].includes(extname(path))) out.push(path);
  }
  return out;
}

interface Preset {
  id: string;
  file: string;
  described: boolean;
  named: boolean;
}

/**
 * Presets declared in a manifest's `frames: [...]`.
 *
 * The array is sliced out by brace depth rather than by regex: a preset body nests
 * several levels deep (`frame.center.children[]`, `docks`), so a lazy match to the
 * next `]` would stop inside the first split's `children`. Within that slice a
 * preset is an `id: '…'` at the array's own indentation.
 */
function presetsIn(file: string, source: string): Preset[] {
  const start = source.indexOf('frames: [');
  if (start === -1) return [];
  let depth = 0;
  let end = -1;
  for (let i = source.indexOf('[', start); i < source.length; i++) {
    const ch = source[i];
    if (ch === '[') depth++;
    else if (ch === ']') {
      depth--;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  if (end === -1) return [];
  const block = source.slice(start, end);
  const out: Preset[] = [];
  for (const chunk of block.split(/\bid:\s*'/).slice(1)) {
    const id = chunk.slice(0, chunk.indexOf("'"));
    // Only up to the next `id:` — a preset's own fields, not its children's.
    const own = chunk.split(/\bid:\s*'/)[0];
    // `backdrop: { id: 'aurora' }` is an id inside a preset, not a preset.
    if (!/\bname:\s*['"]/.test(own)) continue;
    out.push({
      id,
      file,
      named: true,
      described: /\bdescription:\s*['"]/.test(own),
    });
  }
  return out;
}

const presets = walk(MODULES).flatMap((file) => presetsIn(file, readFileSync(file, 'utf8')));

describe('workspace presets', () => {
  it('finds the presets at all', () => {
    // A guard on the guard: if the brace walk or the `frames: [` anchor stops
    // matching, every assertion below passes vacuously.
    expect(presets.length).toBeGreaterThanOrEqual(15);
    expect(presets.map((p) => p.id)).toContain('ai-research');
  });

  it('gives every preset a description', () => {
    const bare = presets.filter((p) => !p.described);
    expect(
      bare.map((p) => `${p.id} (${p.file.slice(REPO.length + 1)})`),
      'A preset with no `description` renders as a bare name in the home launcher ' +
        'and the welcome widget — the two places a user is offered a way of working. ' +
        'Lift one sentence out of the rationale comment above it.',
    ).toEqual([]);
  });
});
