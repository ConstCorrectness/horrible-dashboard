#!/usr/bin/env node
/**
 * Markdown summary of desktop-smoke.mjs / desktop-bench.mjs results — what the
 * desktop workflow writes to the job summary.
 *
 *   node scripts/desktop-report.mjs [results-dir]   (default: desktop-smoke-results)
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const dir = resolve(process.argv[2] ?? 'desktop-smoke-results');
if (!existsSync(dir)) {
  console.log(`_No results in ${dir}._`);
  process.exit(0);
}
const read = (f) => JSON.parse(readFileSync(join(dir, f), 'utf8'));
const files = readdirSync(dir).filter((f) => f.endsWith('.json'));
const out = [];

for (const f of files.filter((f) => f.startsWith('smoke-'))) {
  const r = read(f);
  const passed = r.results.filter((c) => c.ok).length;
  out.push(`### Smoke — ${r.platform} (${passed}/${r.results.length} passed)`, '');
  out.push('| Check | Result | ms |', '| :-- | :-- | --: |');
  for (const c of r.results) {
    const detail = c.ok ? (c.detail ? `✅ ${JSON.stringify(c.detail)}` : '✅') : `❌ ${c.error}`;
    out.push(`| ${c.name} | ${detail.replaceAll('|', '\\|')} | ${c.ms} |`);
  }
  out.push('', `Timings: \`${JSON.stringify(r.timings)}\``, '');
}

const benches = files.filter((f) => f.startsWith('bench-')).map(read);
if (benches.length) {
  const v = (x) => (x === null || x === undefined ? '—' : x);
  const row = (name, pick) => `| ${name} | ${benches.map((b) => v(pick(b))).join(' | ')} |`;
  const idle = (b) => b.phases?.idle?.summary;
  const gl = (b) => b.phases?.webgl?.summary;
  const after = (b) => b.phases?.afterLoad?.summary;
  out.push(`### Benchmark (${benches[0].platform})`, '');
  out.push(`| Metric | ${benches.map((b) => b.label).join(' | ')} |`);
  out.push(`| :-- | ${benches.map(() => '--:').join(' | ')} |`);
  out.push(
    row('Backend healthy (ms after launch)', (b) => b.timings?.backendHealthyMs),
    row('UI ready (ms after launch)', (b) => b.timings?.uiReadyMs),
    row('Shell processes', (b) => idle(b)?.shellProcesses),
    row('Idle: shell working set (MB)', (b) => idle(b)?.shellRssMb.end),
    row('Idle: shell private (MB)', (b) => idle(b)?.shellPrivateMb.end),
    row('Idle: shell private growth (MB/min)', (b) => idle(b)?.shellPrivateGrowthMbPerMin),
    row('Idle: shell CPU (% of a core, avg)', (b) => idle(b)?.shellCpuPctAvg),
    row('Idle: backend working set (MB)', (b) => idle(b)?.backendRssMb),
    row('WebGL: frames per second', (b) => gl(b)?.fps),
    row('WebGL: shell CPU (% of a core, avg)', (b) => gl(b)?.shellCpuPctAvg),
    row('WebGL: shell private peak (MB)', (b) => gl(b)?.shellPrivateMb.peak),
    row('After WebGL: shell private (MB)', (b) => after(b)?.shellPrivateMb.end),
    row('Close: app exited', (b) => b.shutdown?.appExited),
    row('Close: backend stopped', (b) => b.shutdown?.backendStopped),
    row('Close: ms', (b) => b.shutdown?.ms),
    row('Error', (b) => b.error),
  );
  out.push('');
}

console.log(out.join('\n'));
