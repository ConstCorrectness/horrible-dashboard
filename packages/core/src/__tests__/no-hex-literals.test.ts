/**
 * The hardcoded-colour guard.
 *
 * `design-tokens.test.ts` catches a `var()` naming a token that does not exist.
 * It cannot see the other half of the problem: a literal `#161b22` sitting in an
 * inline style object, which names no token at all and so is invisible to the
 * theme switcher by construction.
 *
 * That half was the larger one. Whole modules had been painted in two other
 * products' palettes — GitHub Primer dark (`#8b949e`, `#c9d1d9`, `#30363d`) and
 * Tailwind's defaults (`#38bdf8`, `#f87171`) — and one module had hardcoded the
 * app's *own* midnight values, which is the worst case of all: it looks correct
 * in the default theme and silently ignores the other five. Switching to
 * `daylight` rendered one pane light and the pane beside it fully dark.
 *
 * ## Why this is a baseline and not a clean sweep
 *
 * `games`, `hassault` and `clubhouse` are excluded, and between them they hold
 * the three worst files in the app. That is a deliberate scope decision by the
 * repo owner, not an oversight. The consequence is honest and worth stating: this
 * guard protects new and recently-cleaned code, and does **not** retroactively
 * clean those modules. `BASELINE` is a ratchet — the numbers may fall, never
 * rise — so the excluded modules cannot get worse while they wait.
 *
 * Canvas and WebGL call sites are genuinely exempt: a `CanvasRenderingContext2D`
 * cannot read a CSS custom property, so a literal there is the only option. Those
 * are listed explicitly rather than pattern-matched, so adding one is a decision
 * somebody makes on purpose.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const REPO = join(HERE, '..', '..', '..', '..');

const ROOTS = [
  join(REPO, 'packages', 'core', 'src'),
  join(REPO, 'packages', 'ui', 'src'),
  join(REPO, 'apps', 'web', 'src'),
];

/** Only markup/logic. `.css` is where a theme *declares* its primitives. */
const EXTS = ['.ts', '.tsx'];
const SKIP_DIRS = new Set(['node_modules', 'dist', '__tests__', 'fonts']);

/**
 * Modules held out of the ratchet by an explicit scope decision.
 *
 * Not "allowed" — unmeasured. Removing one from this list is the work of
 * cleaning it; the count below is what that would cost today.
 */
const EXCLUDED_MODULES = ['games', 'hassault', 'clubhouse'];

/**
 * Files that legitimately need a literal colour.
 *
 * A canvas or WebGL context takes a colour string and cannot resolve
 * `var(--accent)`. Anything added here should say, in the file, why it cannot
 * read a token.
 *
 * It is a small bucket (6 at the time of writing) and that is the useful finding:
 * "it has to be a literal because it's drawn to a canvas" turns out to explain
 * almost none of the hardcoded colour in this codebase.
 */
const CANVAS_EXEMPT = [
  'modules/visualizer',
  'modules/model-designer',
  'Avatar3D',
  'provider-marks',
];

/**
 * The ratchet.
 *
 * Lower these when you clean a file; never raise them. A rise means new
 * hardcoded colour landed in code that is supposed to be theme-driven.
 */
const BASELINE: Record<string, number> = {
  // 811 → 793 when the clubhouse room-moderation work's ten new literals were
  // converted to tokens (`#1d2026` and `#2e333d` in it were the app's *own*
  // `--bg-raised` and `--border`, the case this file calls the worst of all), and
  // the eight around them went with them. Lowered rather than left at 801: a
  // ratchet that keeps slack lets the next regression back in unnoticed.
  excluded: 793,
  canvas: 6,
  // 221 → 217 with `viz/uplot-theme.ts`. `MetricsPane` held five of these, and
  // they are worth naming because they were not a shortcut — they were a bug. Its
  // series said `stroke: 'var(--accent, #539bf5)'`, which looks themed and is not:
  // uPlot draws to a canvas, `ctx.strokeStyle` ignores a custom property WITHOUT
  // throwing, and so the fallback was the only colour that ever rendered. A hex
  // count is a decent proxy for "this will not follow the theme"; here it was
  // also pointing at a line drawn in the wrong colour on all six.
  rest: 217,
};

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (EXTS.some((e) => name.endsWith(e))) out.push(full);
  }
  return out;
}

const HEX = /#[0-9a-fA-F]{3,8}\b/g;

function bucketOf(rel: string): 'excluded' | 'canvas' | 'rest' {
  const posix = rel.split(sep).join('/');
  if (EXCLUDED_MODULES.some((m) => posix.includes(`modules/${m}/`))) return 'excluded';
  if (CANVAS_EXEMPT.some((c) => posix.includes(c))) return 'canvas';
  return 'rest';
}

function census() {
  const counts = { excluded: 0, canvas: 0, rest: 0 };
  const offenders: { file: string; n: number }[] = [];
  for (const root of ROOTS) {
    for (const file of walk(root)) {
      const hits = readFileSync(file, 'utf8').match(HEX);
      if (!hits) continue;
      const rel = relative(REPO, file);
      const bucket = bucketOf(rel);
      counts[bucket] += hits.length;
      if (bucket === 'rest') offenders.push({ file: rel, n: hits.length });
    }
  }
  offenders.sort((a, b) => b.n - a.n);
  return { counts, offenders };
}

describe('hardcoded colours', () => {
  const { counts, offenders } = census();

  for (const bucket of ['rest', 'excluded', 'canvas'] as const) {
    it(`does not grow in "${bucket}" (baseline ${BASELINE[bucket]})`, () => {
      const worst =
        bucket === 'rest'
          ? `\nWorst files:\n${offenders
              .slice(0, 8)
              .map((o) => `  ${o.n.toString().padStart(4)}  ${o.file}`)
              .join('\n')}`
          : '';
      expect(
        counts[bucket],
        `Hardcoded colour count in "${bucket}" rose from ${BASELINE[bucket]} to ` +
          `${counts[bucket]}. A literal hex names no token, so it is invisible to the ` +
          'theme switcher and will render identically in all six themes — including ' +
          `the light one, where it is usually wrong. Use a token instead.${worst}`,
      ).toBeLessThanOrEqual(BASELINE[bucket]);
    });
  }

  it('keeps the three cleaned files clean', () => {
    // These were the reachable half of the worst offenders and were cleared to
    // zero. A regression here is a straight undo, so it is worth naming.
    const cleaned = [
      'packages/core/src/modules/evals/EvalsHub.tsx',
      'packages/core/src/modules/localtrack/components/RunDetailsModal.tsx',
      'packages/core/src/modules/trajectories/TrajectoriesHub.tsx',
    ];
    for (const rel of cleaned) {
      const hits = readFileSync(join(REPO, rel), 'utf8').match(HEX) ?? [];
      expect(hits, `${rel} regained hardcoded colours: ${hits.join(', ')}`).toEqual([]);
    }
  });
});
