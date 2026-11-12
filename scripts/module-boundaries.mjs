#!/usr/bin/env node
/**
 * Module boundary ratchet.
 *
 * Counts imports that cross a module boundary the wrong way, and fails when any
 * count rises above the committed baseline (`scripts/module-boundaries.baseline.json`).
 * Moving modules into `modules/<id>` lowers the counts; each migration PR lowers
 * the baseline with `--update`, so the coupling can only go down.
 *
 *   frontendDeep  — a file in packages/core/src/modules/<a>/ importing a file
 *                   inside packages/core/src/modules/<b>/ (b ≠ a)
 *   backendDeep   — a file in backend/modules/<a>/ importing backend.modules.<b>
 *   packageLeaks  — a file in modules/<id>/{web,py}/ importing anything other than
 *                   its own files, the SDK, the kernel, or a third-party package.
 *                   Baseline 0: a module package must never leak.
 *
 *   node scripts/module-boundaries.mjs            check against the baseline
 *   node scripts/module-boundaries.mjs --report   per-module fan-in / fan-out
 *   node scripts/module-boundaries.mjs --update   write the current counts
 */
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BASELINE = join(REPO, 'scripts', 'module-boundaries.baseline.json');
const CORE_MODULES = join(REPO, 'packages', 'core', 'src', 'modules');
const BACKEND_MODULES = join(REPO, 'backend', 'modules');
const PACKAGES = join(REPO, 'modules');

const TS_IMPORT = /(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)/g;
const PY_IMPORT = /^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))/gm;

function walk(dir, exts, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist' || name.startsWith('.')) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, exts, out);
    else if (exts.some((e) => name.endsWith(e)) && !/\.test\.|__tests__|[\\/]tests?[\\/]/.test(full))
      out.push(full);
  }
  return out;
}

const moduleOf = (root, file) => relative(root, file).split(sep)[0];

function frontendDeep() {
  const edges = [];
  for (const file of walk(CORE_MODULES, ['.ts', '.tsx'])) {
    const from = moduleOf(CORE_MODULES, file);
    for (const m of readFileSync(file, 'utf8').matchAll(TS_IMPORT)) {
      const spec = m[1] ?? m[2];
      if (!spec?.startsWith('.')) continue;
      const target = resolve(dirname(file), spec);
      if (!target.startsWith(CORE_MODULES + sep)) continue;
      const to = moduleOf(CORE_MODULES, target);
      if (to !== from) edges.push([from, to]);
    }
  }
  return edges;
}

function backendDeep() {
  const edges = [];
  for (const file of walk(BACKEND_MODULES, ['.py'])) {
    const from = moduleOf(BACKEND_MODULES, file);
    if (from.endsWith('.py')) continue; // loose files (ws.py, ranged_file.py) are shared infra
    for (const m of readFileSync(file, 'utf8').matchAll(PY_IMPORT)) {
      const spec = m[1] ?? m[2];
      const hit = /^backend\.modules\.(\w+)/.exec(spec ?? '');
      if (!hit) continue;
      const to = hit[1];
      if (to !== from && existsSync(join(BACKEND_MODULES, to))) edges.push([from, to]);
    }
  }
  return edges;
}

const WEB_ALLOWED = /^(@horrible\/kernel|@horribledashboard\/sdk)(\/|$)/;
const PY_ALLOWED = /^backend\.(sdk|kernel)(\.|$)/;

function packageLeaks() {
  const leaks = [];
  if (!existsSync(PACKAGES)) return leaks;
  for (const id of readdirSync(PACKAGES)) {
    const pkg = join(PACKAGES, id);
    for (const file of walk(join(pkg, 'web'), ['.ts', '.tsx'])) {
      for (const m of readFileSync(file, 'utf8').matchAll(TS_IMPORT)) {
        const spec = m[1] ?? m[2];
        if (!spec) continue;
        if (spec.startsWith('.')) {
          if (!resolve(dirname(file), spec).startsWith(pkg + sep)) leaks.push(`${relative(REPO, file)} → ${spec}`);
        } else if (spec.startsWith('@horrible/') && !WEB_ALLOWED.test(spec)) {
          leaks.push(`${relative(REPO, file)} → ${spec}`);
        }
      }
    }
    for (const file of walk(join(pkg, 'py'), ['.py'])) {
      for (const m of readFileSync(file, 'utf8').matchAll(PY_IMPORT)) {
        const spec = m[1] ?? m[2];
        if (spec?.startsWith('backend') && !PY_ALLOWED.test(spec)) leaks.push(`${relative(REPO, file)} → ${spec}`);
      }
    }
  }
  return leaks;
}

function report(name, edges) {
  const fanIn = new Map();
  const fanOut = new Map();
  for (const [a, b] of edges) {
    fanOut.set(a, (fanOut.get(a) ?? 0) + 1);
    fanIn.set(b, (fanIn.get(b) ?? 0) + 1);
  }
  const top = (m) =>
    [...m.entries()]
      .sort((x, y) => y[1] - x[1])
      .slice(0, 15)
      .map(([k, v]) => `${k} ${v}`)
      .join(', ');
  console.log(`${name}: ${edges.length} edges`);
  console.log(`  fan-in : ${top(fanIn)}`);
  console.log(`  fan-out: ${top(fanOut)}`);
}

const fe = frontendDeep();
const be = backendDeep();
const leaks = packageLeaks();
const counts = { frontendDeep: fe.length, backendDeep: be.length, packageLeaks: leaks.length };
const args = new Set(process.argv.slice(2));

if (args.has('--report')) {
  report('frontendDeep', fe);
  report('backendDeep', be);
  console.log(`packageLeaks: ${leaks.length}`);
  for (const l of leaks) console.log(`  ${l}`);
  process.exit(0);
}

if (args.has('--update')) {
  writeFileSync(BASELINE, `${JSON.stringify({ ...counts, packageLeaks: 0 }, null, 2)}\n`);
  console.log('module-boundaries: baseline written', counts);
  if (leaks.length) {
    console.error('module-boundaries: package leaks are never baselined:');
    for (const l of leaks) console.error(`  ${l}`);
    process.exit(1);
  }
  process.exit(0);
}

const baseline = existsSync(BASELINE) ? JSON.parse(readFileSync(BASELINE, 'utf8')) : null;
if (!baseline) {
  console.error('module-boundaries: no baseline; run with --update');
  process.exit(1);
}
let failed = false;
for (const [k, v] of Object.entries(counts)) {
  const limit = baseline[k] ?? 0;
  if (v > limit) {
    failed = true;
    console.error(`module-boundaries: ${k} rose to ${v} (baseline ${limit})`);
  } else if (v < limit) {
    console.log(`module-boundaries: ${k} fell to ${v} (baseline ${limit}) — lower it with --update`);
  }
}
for (const l of leaks) console.error(`  leak: ${l}`);
if (failed) {
  console.error('A module may reach another only through @horrible/kernel or a service contract.');
  process.exit(1);
}
console.log('module-boundaries: ok', counts);
