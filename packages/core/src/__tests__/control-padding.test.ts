/**
 * The control-padding guard.
 *
 * `controls.css` gives every text-ish `input` and every `select` a **fixed
 * `height: var(--control-h)`** (30px) with the vertical padding taken out — the
 * One Height Rule, which is the only reason a row of mixed controls lines up
 * (docs/architecture/theming.mdx#one-control-height).
 *
 * The trap is that an inline `padding: '0.5rem'` looks like it makes the control
 * roomier and does the opposite: the height is already fixed, so padding does not
 * grow the box, it eats it. `30 − 16 − 2 = 12px` of content box for 12.8px text —
 * the top padding pushes the label down and it clips along the bottom edge.
 *
 * It ships unnoticed because the damage is uneven. A text input under the same
 * padding merely looks tight; a `<select>` clips, because native rendering is also
 * reserving room for its own arrow. So the report is always "the dropdowns look
 * wrong" and the inputs beside them are quietly wrong too. That is exactly how it
 * reached the Clubhouse Voice Agent panel and stayed there.
 *
 * Zero tolerance rather than a ratchet: every occurrence was fixed when this was
 * written, so there is no baseline to hold and a new one is always a regression.
 *
 * **What it cannot see.** Padding that arrives through a CSS class, or through a
 * style object built somewhere other than the file that renders the control. The
 * two shapes below are where all fourteen real occurrences lived, so this covers
 * the paths that actually get used — it is not a proof.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const REPO = join(HERE, '..', '..', '..', '..');

const ROOTS = [
  join(REPO, 'packages', 'core', 'src'),
  join(REPO, 'packages', 'ui', 'src'),
  join(REPO, 'apps', 'web', 'src'),
];

const SKIP_DIRS = new Set(['node_modules', 'dist', '__tests__', 'fonts']);

/**
 * Input types the One Height Rule deliberately leaves alone, so padding on them
 * is nobody's bug: they keep their intrinsic sizing (`controls.css` lists the
 * text-ish types explicitly for this reason). `textarea` is excluded the same way
 * — it is `height: auto` and has to grow, so it keeps its own vertical padding.
 */
const INTRINSIC_TYPES = new Set(['checkbox', 'radio', 'range', 'color', 'file', 'image']);

interface Finding {
  file: string;
  line: number;
  element: string;
  padding: string;
  via: 'inline' | string;
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (name.endsWith('.tsx')) out.push(full);
  }
  return out;
}

/** The text of a JSX opening tag starting at `from`, brace-aware. */
function openingTag(src: string, from: number): string {
  let depth = 0;
  for (let i = from; i < src.length; i++) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    else if (ch === '>' && depth === 0) return src.slice(from, i);
  }
  return src.slice(from, from + 2000);
}

/** A `padding`-ish property whose *vertical* component is non-zero, or null. */
function verticalPadding(styleText: string): string | null {
  const m = /padding(?:Block|Top|Bottom)?:\s*'([^']+)'/.exec(styleText);
  if (!m) return null;
  const first = m[1].trim().split(/\s+/)[0];
  // `padding: '0 0.6rem'` and `padding: '0'` are the shapes we want; anything
  // else puts space above and below inside a box that cannot grow.
  //
  // The zero test has to be anchored at BOTH ends. `/^0(\D|$)/` reads `0.5rem`
  // as zero — the `.` satisfies `\D` — which silently exempted the exact value
  // that caused the bug this guard is named after. It was a real false negative
  // here before it was a comment.
  return /^0(?:\.0+)?(?:px|rem|em|%|vh|vw|ch)?$/.test(first) ? null : m[1];
}

/** The body of `const <name> ... = { ... }` in `src`, or null. */
function styleConstBody(src: string, name: string): string | null {
  const decl = new RegExp(`const\\s+${name}\\b[^=]*=\\s*\\{`).exec(src);
  if (!decl) return null;
  let depth = 0;
  for (let i = decl.index + decl[0].length - 1; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(decl.index, i + 1);
  }
  return null;
}

function scan(file: string): Finding[] {
  const src = readFileSync(file, 'utf8');
  const rel = relative(REPO, file).split('\\').join('/');
  const found: Finding[] = [];

  for (const m of src.matchAll(/<(input|select)\b/g)) {
    const tag = openingTag(src, m.index! + m[0].length);
    const typeMatch = /type="([a-z]+)"/.exec(tag);
    if (typeMatch && INTRINSIC_TYPES.has(typeMatch[1])) continue;
    const line = src.slice(0, m.index!).split('\n').length;

    const inline = verticalPadding(tag);
    if (inline) {
      found.push({ file: rel, line, element: m[1], padding: inline, via: 'inline' });
      continue;
    }
    // `style={someSharedObject}` — the shape the Clubhouse panel used, where one
    // object was wrong and every control in the tab inherited it.
    const ref = /style=\{([A-Za-z_$][\w$]*)\}/.exec(tag);
    if (!ref) continue;
    const body = styleConstBody(src, ref[1]);
    if (!body) continue;
    const shared = verticalPadding(body);
    if (shared) {
      found.push({ file: rel, line, element: m[1], padding: shared, via: ref[1] });
    }
  }
  return found;
}

describe('control padding', () => {
  it('no input or select adds vertical padding to its fixed height', () => {
    const findings = ROOTS.flatMap((root) => walk(root)).flatMap(scan);
    const report = findings
      .map((f) => `${f.file}:${f.line} <${f.element}> padding: '${f.padding}' (via ${f.via})`)
      .join('\n');
    expect(
      report,
      'Controls have a fixed height (--control-h) with no vertical padding — see ' +
        'docs/architecture/theming.mdx#one-control-height. Vertical padding shrinks the ' +
        'content box instead of growing the control, and a <select> clips. Use horizontal ' +
        "padding only, e.g. padding: '0 0.6rem'.",
    ).toBe('');
  });
});
