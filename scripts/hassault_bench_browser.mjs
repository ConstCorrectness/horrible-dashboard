#!/usr/bin/env node
/**
 * Fly the browser client's benchmark path over every map and tier.
 *
 * The browser half of `scripts/hassault_bench_native.py`: opens assault-web in
 * a **headed** Chromium (a hidden or headless page gets no
 * `requestAnimationFrame` worth measuring) at 1920x1080, one map at a time
 * through the offline training link (`#map=<id>&mode=train`), and calls
 * `window.__hassaultBench(map)` once the map is up. Vsync and the frame-rate
 * limit are off so the frame interval is the cost, as it is natively, and the
 * high-performance GPU is asked for so a laptop does not bench its iGPU.
 *
 * Needs assault-web pointed at a node that serves the maps — the
 * `assault-web-bench` launch config (port 5180, backend on 8123).
 *
 *   node scripts/hassault_bench_browser.mjs --url http://127.0.0.1:5180
 *
 * Writes one JSON object per line to `--out` and prints the same Markdown
 * table the native driver does.
 */
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync, appendFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
// Playwright is a dependency of the packages that drive browsers, not of the
// root, so it is resolved from one of them.
const { chromium } = createRequire(join(ROOT, 'packages/webml/package.json'))('playwright');

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

const url = arg('url', 'http://127.0.0.1:5180');
const tiers = arg('tiers', 'medium,high').split(',').filter(Boolean);
const paths = JSON.parse(
  readFileSync(join(ROOT, 'packages/core/src/modules/hassault/bench-paths.json'), 'utf8'),
);
const maps = (arg('maps', '') || Object.keys(paths.maps).sort().join(','))
  .split(',')
  .filter(Boolean);
const out = arg('out', 'hassault-bench-browser.jsonl');
const loadTimeoutMs = Number(arg('load-timeout', '180')) * 1000;

/** Call the bench, retrying while the map or the renderer is still coming up. */
async function benchWhenReady(page, map) {
  const deadline = Date.now() + loadTimeoutMs;
  for (;;) {
    const r = await page.evaluate(async (m) => {
      const bench = window.__hassaultBench;
      if (!bench) return { retry: 'no bench hook yet' };
      try {
        return { result: await bench(m) };
      } catch (e) {
        return { retry: String(e && e.message ? e.message : e) };
      }
    }, map);
    if (r.result) return r.result;
    // "a bench is already running" or a bad map name will not fix itself.
    if (/already running|no bench path/.test(r.retry)) throw new Error(r.retry);
    if (Date.now() > deadline) throw new Error(`gave up waiting: ${r.retry}`);
    await page.waitForTimeout(1000);
  }
}

function table(results) {
  const rows = results.map((r) => {
    const gpu =
      r.gpuP50 != null && r.gpuP99 != null ? `${r.gpuP50.toFixed(2)} / ${r.gpuP99.toFixed(2)}` : 'n/a';
    return (
      `| ${r.map} | ${r.client} | ${r.tier} | ${r.width}x${r.height} | ${r.fps.toFixed(0)} ` +
      `| ${r.p50.toFixed(2)} | ${r.p99.toFixed(2)} | ${r.cpuP50.toFixed(2)} / ${r.cpuP99.toFixed(2)} ` +
      `| ${gpu} | ${r.drawCallsMax} | ${Math.floor(r.trianglesMax / 1000)}k |`
    );
  });
  return [
    '| Map | Client | Tier | Res | fps | p50 ms | p99 ms | CPU p50/p99 ms | GPU p50/p99 ms | Draws (max) | Tris (max) |',
    '|---|---|---|---|---|---|---|---|---|---|---|',
    ...rows,
  ].join('\n');
}

const browser = await chromium.launch({
  headless: false,
  args: [
    '--force_high_performance_gpu',
    '--disable-gpu-vsync',
    '--disable-frame-rate-limit',
    '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding',
    '--window-size=1940,1200',
  ],
});
writeFileSync(out, '');
const results = [];
let failures = 0;
try {
  for (const tier of tiers) {
    for (const map of maps) {
      // A fresh context per run: nothing cached from the last map, and the
      // tier and the guest choice set before the page's first script reads them.
      const context = await browser.newContext({
        viewport: { width: 1920, height: 1080 },
        deviceScaleFactor: 1,
      });
      await context.addInitScript((t) => {
        localStorage.setItem('hassault_guest_chosen', '1');
        localStorage.setItem('hassault.graphics', t);
      }, tier);
      const page = await context.newPage();
      try {
        process.stderr.write(`bench ${map} ${tier}\n`);
        await page.goto(`${url}/#map=${map}&mode=train`);
        const deploy = page.getByRole('button', { name: 'Deploy', exact: true });
        await deploy.waitFor({ timeout: loadTimeoutMs });
        await deploy.click();
        const result = await benchWhenReady(page, map);
        if (results.length === 0) {
          const gpu = await page.evaluate(() => {
            const gl = document.createElement('canvas').getContext('webgl2');
            const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
            return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'unknown';
          });
          process.stderr.write(`  renderer: ${gpu}\n`);
        }
        results.push(result);
        appendFileSync(out, `${JSON.stringify(result)}\n`);
      } catch (e) {
        failures += 1;
        process.stderr.write(`  ${map} ${tier}: ${e.message}\n`);
      } finally {
        await context.close();
      }
    }
  }
} finally {
  await browser.close();
}
console.log(table(results));
process.exit(failures ? 1 : 0);
