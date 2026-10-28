#!/usr/bin/env node
/**
 * Measure a packaged desktop build from the outside: boot time, memory and CPU of
 * its whole process tree, at idle and under a WebGL load, and whether it shuts its
 * backend down when closed.
 *
 *   node scripts/desktop-bench.mjs --exe <path> --label electron [--idle 60] [--load 30]
 *     [--cwd <checkout>]   # whose backend the app runs (default: this checkout)
 *
 * Shell-agnostic on purpose — the same procedure measures the Electron shell and a
 * Tauri build of an older commit, so the two are compared like for like:
 *
 * - The app starts with a checkout as its working directory (both shells then run
 *   that checkout's backend), on a fixed backend port, with a throwaway
 *   HORRIBLE_DATA_DIR.
 * - "UI ready" is the app shell's root element existing in the page, read over the
 *   Chrome DevTools Protocol on a debugging port (`--remote-debugging-port` for
 *   Electron, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS for WebView2).
 * - Memory and CPU are summed over the app's process tree, split into the shell
 *   (everything but the backend) and the backend (the Python subtree), sampled from
 *   the OS: Win32_Process on Windows (working set, private bytes, CPU time), /proc
 *   on Linux (RSS, and PSS as the "private" figure), ps on macOS. CPU is reported
 *   in percent of one core.
 * - The load phase draws a full-window WebGL scene in the page for `--load` seconds,
 *   the case the move away from WebKitGTK was about.
 *
 * Writes desktop-smoke-results/bench-<label>-<platform>.json and prints a summary.
 */
import { execFileSync, spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const isWin = process.platform === 'win32';

const argv = process.argv.slice(2);
const option = (name, fallback) => {
  const i = argv.indexOf(name);
  return i === -1 ? fallback : argv[i + 1];
};
// Absolute, because the app is spawned with `cwd: appCwd`: Windows resolves a
// relative executable against the child's cwd, not ours, so `--exe tauri-baseline/…`
// with `--cwd tauri-baseline` looked for tauri-baseline/tauri-baseline/… (ENOENT).
const exeArg = option('--exe');
const exe = exeArg && resolve(exeArg);
const label = option('--label', 'app');
const idleSec = Number(option('--idle', '60'));
const loadSec = Number(option('--load', '30'));
const backendPort = Number(option('--backend-port', '8765'));
const cdpPort = Number(option('--cdp-port', '9333'));
// The checkout whose backend the app runs: this one by default. A build of another
// commit must run its own (its backend allows its own UI's origin, and no other).
const appCwd = resolve(option('--cwd', REPO));
if (!exe) {
  console.error('usage: desktop-bench.mjs --exe <path> --label <name> [--idle 60] [--load 30]');
  process.exit(2);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --- process tree sampling ----------------------------------------------------

/** Every process: pid, ppid, name, rss (bytes), private (bytes), cpu (seconds). */
function processTable() {
  if (isWin) {
    const ps =
      'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize,PrivatePageCount,KernelModeTime,UserModeTime | ConvertTo-Json -Compress';
    const out = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps], {
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
      windowsHide: true,
    });
    return JSON.parse(out).map((p) => ({
      pid: p.ProcessId,
      ppid: p.ParentProcessId,
      name: String(p.Name ?? ''),
      rss: Number(p.WorkingSetSize ?? 0),
      // PrivatePageCount is bytes, despite the name.
      private: Number(p.PrivatePageCount ?? 0),
      // 100-ns units.
      cpu: (Number(p.KernelModeTime ?? 0) + Number(p.UserModeTime ?? 0)) / 1e7,
    }));
  }
  if (process.platform === 'linux') return procTable();
  // macOS: `time` is [[dd-]hh:]mm:ss.cc of CPU.
  const out = execFileSync('ps', ['-axo', 'pid=,ppid=,rss=,time=,comm='], { encoding: 'utf8' });
  return out
    .trim()
    .split('\n')
    .map((line) => {
      const [pid, ppid, rss, time, ...name] = line.trim().split(/\s+/);
      const [days, clock] = time.includes('-') ? time.split('-') : ['0', time];
      const cpu =
        Number(days) * 86400 + clock.split(':').reduce((acc, part) => acc * 60 + Number(part), 0);
      return {
        pid: Number(pid),
        ppid: Number(ppid),
        name: name.join(' ').split('/').pop(),
        rss: Number(rss) * 1024,
        private: Number(rss) * 1024,
        cpu,
      };
    });
}

/** Linux: /proc directly, for CPU time in clock ticks rather than ps's whole seconds. */
function procTable() {
  const TICKS = 100;
  const PAGE = 4096;
  const rows = [];
  for (const entry of readdirSync('/proc')) {
    if (!/^\d+$/.test(entry)) continue;
    try {
      const stat = readFileSync(`/proc/${entry}/stat`, 'utf8');
      const close = stat.lastIndexOf(')');
      const name = stat.slice(stat.indexOf('(') + 1, close);
      const fields = stat.slice(close + 2).split(' ');
      // After "pid (comm) ": state ppid … utime(14) stime(15) … rss(24), 1-based.
      const ppid = Number(fields[1]);
      const cpu = (Number(fields[11]) + Number(fields[12])) / TICKS;
      const rss = Number(fields[21]) * PAGE;
      // PSS, not RSS, for the "private" column: Chromium's processes share most of
      // their pages, and summing RSS counts those once per process.
      let pss = rss;
      try {
        const rollup = readFileSync(`/proc/${entry}/smaps_rollup`, 'utf8');
        const kb = /^Pss:\s+(\d+) kB/m.exec(rollup);
        if (kb) pss = Number(kb[1]) * 1024;
      } catch {
        // Not readable (another user's process); RSS will do.
      }
      rows.push({ pid: Number(entry), ppid, name, rss, private: pss, cpu });
    } catch {
      // Exited while we looked.
    }
  }
  return rows;
}

const isBackend = (p) => /^(python|python3|uv|uvicorn)(\.exe)?$/i.test(p.name);

/** The tree under `root`, split into shell and backend subtrees. */
function tree(root) {
  const table = processTable();
  const children = new Map();
  for (const p of table) {
    if (!children.has(p.ppid)) children.set(p.ppid, []);
    children.get(p.ppid).push(p);
  }
  const shell = [];
  const backend = [];
  const walk = (pid, inBackend) => {
    for (const child of children.get(pid) ?? []) {
      const b = inBackend || isBackend(child);
      (b ? backend : shell).push(child);
      walk(child.pid, b);
    }
  };
  const self = table.find((p) => p.pid === root);
  if (self) shell.push(self);
  walk(root, false);
  return { shell, backend };
}

const sum = (list, key) => list.reduce((acc, p) => acc + p[key], 0);
const mb = (bytes) => Math.round((bytes / 1024 / 1024) * 10) / 10;

async function samplePhase(root, seconds, intervalMs = 3_000) {
  const samples = [];
  let previous = null;
  const end = Date.now() + seconds * 1000;
  while (Date.now() < end) {
    const t = Date.now();
    const { shell, backend } = tree(root);
    const current = { t, shellCpu: sum(shell, 'cpu'), backendCpu: sum(backend, 'cpu') };
    const sample = {
      t,
      shellProcesses: shell.length,
      shellRssMb: mb(sum(shell, 'rss')),
      shellPrivateMb: mb(sum(shell, 'private')),
      backendRssMb: mb(sum(backend, 'rss')),
    };
    if (previous) {
      const wall = (t - previous.t) / 1000;
      sample.shellCpuPct = Math.round(((current.shellCpu - previous.shellCpu) / wall) * 1000) / 10;
      sample.backendCpuPct =
        Math.round(((current.backendCpu - previous.backendCpu) / wall) * 1000) / 10;
    }
    samples.push(sample);
    previous = current;
    await sleep(Math.max(0, intervalMs - (Date.now() - t)));
  }
  return samples;
}

function summarize(samples) {
  const cpu = samples.filter((s) => s.shellCpuPct !== undefined);
  const avg = (list, key) =>
    Math.round((list.reduce((a, s) => a + s[key], 0) / list.length) * 10) / 10;
  const first = samples[0];
  const last = samples.at(-1);
  const minutes = (last.t - first.t) / 60_000;
  return {
    seconds: Math.round((last.t - first.t) / 1000),
    shellProcesses: last.shellProcesses,
    shellRssMb: {
      start: first.shellRssMb,
      end: last.shellRssMb,
      peak: Math.max(...samples.map((s) => s.shellRssMb)),
    },
    shellPrivateMb: {
      start: first.shellPrivateMb,
      end: last.shellPrivateMb,
      peak: Math.max(...samples.map((s) => s.shellPrivateMb)),
    },
    shellPrivateGrowthMbPerMin:
      minutes > 0
        ? Math.round(((last.shellPrivateMb - first.shellPrivateMb) / minutes) * 10) / 10
        : 0,
    backendRssMb: last.backendRssMb,
    shellCpuPctAvg: cpu.length ? avg(cpu, 'shellCpuPct') : null,
    shellCpuPctPeak: cpu.length ? Math.max(...cpu.map((s) => s.shellCpuPct)) : null,
    backendCpuPctAvg: cpu.length ? avg(cpu, 'backendCpuPct') : null,
  };
}

// --- the page, over CDP ---------------------------------------------------------

async function pageTarget(child, deadline) {
  // What the port last said, for the error: "nothing listening" and "no page among
  // these targets" are different failures.
  let last = 'nothing listening';
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`app exited (${child.exitCode}) before a page target appeared`);
    }
    try {
      const targets = await (await fetch(`http://127.0.0.1:${cdpPort}/json/list`)).json();
      const page = targets.find(
        (t) => t.type === 'page' && !t.url.startsWith('devtools:') && t.webSocketDebuggerUrl,
      );
      if (page) return page;
      last = `targets ${JSON.stringify(targets.map((t) => `${t.type} ${t.url}`))}`;
    } catch (err) {
      last = String(err?.cause?.code ?? err?.message ?? err);
    }
    await sleep(100);
  }
  throw new Error(`no page target on the debugging port (last: ${last})`);
}

/**
 * Command lines of the WebView2 browser processes (Windows), for a failed run: whether
 * `--remote-debugging-port` reached the browser at all is the first question.
 */
function webview2Browsers() {
  if (!isWin) return [];
  const ps =
    "Get-CimInstance Win32_Process -Filter \"Name='msedgewebview2.exe'\" | Where-Object { $_.CommandLine -notmatch '--type=' } | ForEach-Object { $_.CommandLine }";
  try {
    return execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps], {
      encoding: 'utf8',
      windowsHide: true,
    })
      .split(/\r?\n/)
      .filter(Boolean);
  } catch {
    return [];
  }
}

function cdpSession(wsUrl) {
  return new Promise((resolveSession, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) rej(new Error(msg.error.message));
        else res(msg.result);
      }
    };
    ws.onerror = () => reject(new Error('CDP socket error'));
    ws.onopen = () =>
      resolveSession({
        send: (method, params = {}) =>
          new Promise((res, rej) => {
            id += 1;
            pending.set(id, { res, rej });
            ws.send(JSON.stringify({ id, method, params }));
          }),
        evaluate: async (expression) => {
          const r = await new Promise((res, rej) => {
            id += 1;
            pending.set(id, { res, rej });
            ws.send(
              JSON.stringify({
                id,
                method: 'Runtime.evaluate',
                params: { expression, returnByValue: true, awaitPromise: true },
              }),
            );
          });
          return r.result?.value;
        },
        close: () => ws.close(),
      });
  });
}

/** A full-window WebGL scene: a few thousand instanced triangles, redrawn every frame. */
const WEBGL_LOAD = `(() => {
  const c = document.createElement('canvas');
  c.id = '__bench_gl';
  Object.assign(c.style, { position: 'fixed', inset: '0', width: '100vw', height: '100vh', zIndex: 99999, pointerEvents: 'none' });
  c.width = innerWidth * devicePixelRatio; c.height = innerHeight * devicePixelRatio;
  document.body.appendChild(c);
  const gl = c.getContext('webgl2');
  if (!gl) return 'no webgl2';
  const vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, '#version 300 es\\nin vec2 p; uniform float t; out vec3 col; void main(){ float i=float(gl_InstanceID); vec2 o=vec2(sin(i*1.7+t), cos(i*2.3+t*1.3))*0.9; gl_Position=vec4(p*0.04+o,0,1); col=vec3(fract(i*0.13),fract(i*0.37),fract(i*0.71)); }');
  gl.compileShader(vs);
  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, '#version 300 es\\nprecision mediump float; in vec3 col; out vec4 o; void main(){ o=vec4(col,0.6); }');
  gl.compileShader(fs);
  const pr = gl.createProgram(); gl.attachShader(pr, vs); gl.attachShader(pr, fs); gl.linkProgram(pr); gl.useProgram(pr);
  const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0,1,-1,-1,1,-1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(pr, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const tl = gl.getUniformLocation(pr, 't');
  gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  window.__benchFrames = 0;
  const frame = (ts) => {
    if (!document.getElementById('__bench_gl')) return;
    gl.viewport(0, 0, c.width, c.height); gl.clearColor(0.05,0.05,0.1,1); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform1f(tl, ts / 1000); gl.drawArraysInstanced(gl.TRIANGLES, 0, 3, 4000);
    window.__benchFrames++; requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
  return 'ok';
})()`;

// --- the run --------------------------------------------------------------------

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), `hd-bench-${label}-`));
  const env = {
    ...process.env,
    HORRIBLE_DEV_BACKEND_PORT: String(backendPort),
    HORRIBLE_DEV_HOST: '127.0.0.1',
    HORRIBLE_DATA_DIR: dataDir,
    // WebView2 reads extra browser arguments from here; Electron ignores it.
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${cdpPort}`,
  };
  const args = [`--remote-debugging-port=${cdpPort}`];
  if (process.platform === 'linux') args.push('--no-sandbox');

  console.log(`\nBenchmark: ${label} (${process.platform}) — ${exe}`);
  const t0 = Date.now();
  const child = spawn(exe, args, { cwd: appCwd, env, stdio: 'ignore', windowsHide: false });
  const result = { label, platform: process.platform, exe, timings: {}, phases: {} };
  let session = null;

  try {
    // Backend: the port answering /api/health.
    const deadline = t0 + 240_000;
    for (;;) {
      const ok = await fetch(`http://127.0.0.1:${backendPort}/api/health`).then(
        (r) => r.ok,
        () => false,
      );
      if (ok) break;
      if (Date.now() > deadline) throw new Error('backend never became healthy');
      if (child.exitCode !== null) throw new Error(`app exited (${child.exitCode})`);
      await sleep(100);
    }
    result.timings.backendHealthyMs = Date.now() - t0;

    // UI: the shell's root element in the page.
    const target = await pageTarget(child, deadline);
    result.timings.pageTargetMs = Date.now() - t0;
    session = await cdpSession(target.webSocketDebuggerUrl);
    while (!(await session.evaluate(`!!document.querySelector('.shell--frame')`))) {
      if (Date.now() > deadline) throw new Error('UI never booted');
      await sleep(100);
    }
    result.timings.uiReadyMs = Date.now() - t0;
    result.pageUrl = target.url;
    result.userAgent = await session.evaluate('navigator.userAgent');
    console.log('  timings', result.timings);

    console.log('  settling 10 s…');
    await sleep(10_000);
    console.log(`  idle ${idleSec} s…`);
    const idle = await samplePhase(child.pid, idleSec);
    result.phases.idle = { summary: summarize(idle), samples: idle };
    console.log('  idle', result.phases.idle.summary);

    if (loadSec > 0) {
      const started = await session.evaluate(WEBGL_LOAD);
      console.log(`  WebGL load ${loadSec} s (${started})…`);
      const load = await samplePhase(child.pid, loadSec);
      const frames = await session.evaluate('window.__benchFrames || 0');
      await session.evaluate(`document.getElementById('__bench_gl')?.remove()`);
      result.phases.webgl = {
        summary: {
          ...summarize(load),
          fps: Math.round((frames / loadSec) * 10) / 10,
          start: started,
        },
        samples: load,
      };
      console.log('  webgl', result.phases.webgl.summary);
      await sleep(5_000);
      const after = await samplePhase(child.pid, 15);
      result.phases.afterLoad = { summary: summarize(after), samples: after };
      console.log('  after load', result.phases.afterLoad.summary);
    }
  } catch (err) {
    result.error = String(err?.message ?? err);
    console.error('  ✗', result.error);
    result.diagnostics = {
      appRunning: child.exitCode === null,
      shellProcesses: tree(child.pid).shell.map((p) => p.name),
      webview2Browsers: webview2Browsers(),
    };
    console.error('  diagnostics', JSON.stringify(result.diagnostics, null, 2));
  } finally {
    session?.close();
    const backendPids = tree(child.pid).backend.map((p) => p.pid);
    // A polite close, the way a user's click on × arrives (WM_CLOSE / SIGTERM), then
    // check that the backend went with it.
    const closeStarted = Date.now();
    if (isWin) {
      try {
        execFileSync('taskkill', ['/PID', String(child.pid)], { stdio: 'ignore' });
      } catch {
        // Already gone.
      }
    } else {
      child.kill('SIGTERM');
    }
    const exited = await Promise.race([
      new Promise((r) => (child.exitCode !== null ? r(true) : child.once('exit', () => r(true)))),
      sleep(20_000).then(() => false),
    ]);
    let backendDown = false;
    while (Date.now() - closeStarted < 20_000) {
      backendDown = !(await fetch(`http://127.0.0.1:${backendPort}/api/health`).then(
        () => true,
        () => false,
      ));
      if (backendDown) break;
      await sleep(250);
    }
    result.shutdown = {
      appExited: exited,
      backendStopped: backendDown,
      ms: Date.now() - closeStarted,
    };
    console.log('  shutdown', result.shutdown);
    if (isWin) {
      try {
        execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
      } catch {
        // Already gone.
      }
    } else if (child.exitCode === null) {
      child.kill('SIGKILL');
    }
    if (!backendDown) {
      // Leave the runner clean for the next measurement — this app's backend only.
      for (const pid of backendPids) {
        try {
          process.kill(pid, 'SIGKILL');
        } catch {
          // Already gone.
        }
      }
    }
    rmSync(dataDir, { recursive: true, force: true, maxRetries: 5 });
  }

  const outDir = join(REPO, 'desktop-smoke-results');
  mkdirSync(outDir, { recursive: true });
  writeFileSync(
    join(outDir, `bench-${label}-${process.platform}.json`),
    JSON.stringify(result, null, 2),
  );
  process.exit(result.error ? 1 : 0);
}

main().catch((err) => {
  console.error(err);
  process.exit(100);
});
