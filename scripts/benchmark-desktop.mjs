#!/usr/bin/env node
/**
 * Cross-platform desktop shell benchmark suite (Windows & Linux).
 *
 * Compares Electron vs Tauri performance across:
 * 1. Startup latency (process spawn -> frontend ready -> backend ready)
 * 2. Memory footprint & leak detection (total RSS / WorkingSet across process tree)
 * 3. Memory slope over time (measuring WebGL / backdrop buffer leak rates in MB/min)
 * 4. Average and peak CPU utilization
 *
 * Usage:
 *   node scripts/benchmark-desktop.mjs --shell electron --duration 30
 *   node scripts/benchmark-desktop.mjs --shell tauri --duration 30
 *   node scripts/benchmark-desktop.mjs --compare --duration 30
 */

import { spawn, execSync, spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as path from 'node:path';
import { performance } from 'node:perf_hooks';

const isWin = process.platform === 'win32';

// Parse CLI flags
const args = process.argv.slice(2);

if (args.includes('--help') || args.includes('-h')) {
  console.log(`
Horrible Dashboard Desktop Benchmark Suite

Usage:
  node scripts/benchmark-desktop.mjs [options]

Options:
  --shell <electron|tauri>   Target shell to benchmark (default: electron)
  --compare                  Run sequential comparison between Tauri and Electron
  --duration <seconds>       Duration to sample metrics in seconds (default: 30)
  -h, --help                 Show this help message
`);
  process.exit(0);
}

const compare = args.includes('--compare');
const durationIdx = args.indexOf('--duration');
const durationSec = durationIdx !== -1 ? Number(args[durationIdx + 1]) || 30 : 30;
const shellIdx = args.indexOf('--shell');
const targetShell = shellIdx !== -1 ? args[shellIdx + 1]?.toLowerCase() : 'electron';

/**
 * Poll an HTTP URL until it returns 200 or timeout.
 */
function pollUrl(urlStr, timeoutMs = 45000) {
  const start = performance.now();
  const url = new URL(urlStr);
  return new Promise((resolve) => {
    const check = () => {
      const req = http.get(url, { timeout: 1000 }, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 400) {
          resolve(performance.now() - start);
          return;
        }
        if (performance.now() - start > timeoutMs) {
          resolve(null);
          return;
        }
        setTimeout(check, 200);
      });
      req.on('error', () => {
        if (performance.now() - start > timeoutMs) {
          resolve(null);
          return;
        }
        setTimeout(check, 200);
      });
      req.on('timeout', () => {
        req.destroy();
        if (performance.now() - start > timeoutMs) {
          resolve(null);
          return;
        }
        setTimeout(check, 200);
      });
    };
    check();
  });
}

/**
 * Get all descendant process IDs of a root PID.
 */
function getProcessTreePids(rootPid) {
  const pids = new Set([rootPid]);
  if (!rootPid) return [];

  if (isWin) {
    try {
      const out = execSync(
        `powershell -NoProfile -Command "Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId | ConvertTo-Json"`,
        { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] },
      );
      const parsed = JSON.parse(out);
      const list = Array.isArray(parsed) ? parsed : [parsed];
      let added = true;
      while (added) {
        added = false;
        for (const proc of list) {
          if (pids.has(proc.ParentProcessId) && !pids.has(proc.ProcessId)) {
            pids.add(proc.ProcessId);
            added = true;
          }
        }
      }
    } catch {
      // fallback to single pid
    }
  } else {
    try {
      const out = execSync(`ps -eo pid,ppid --no-headers`, {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'ignore'],
      });
      const lines = out.trim().split('\n');
      const pairs = lines.map((l) => {
        const parts = l.trim().split(/\s+/);
        return { pid: Number(parts[0]), ppid: Number(parts[1]) };
      });
      let added = true;
      while (added) {
        added = false;
        for (const proc of pairs) {
          if (pids.has(proc.ppid) && !pids.has(proc.pid)) {
            pids.add(proc.pid);
            added = true;
          }
        }
      }
    } catch {
      // fallback to single pid
    }
  }

  return Array.from(pids);
}

/**
 * Sample memory (MB) and CPU (%) for a list of PIDs.
 */
function sampleProcessTreeMetrics(pids) {
  if (!pids || pids.length === 0) return { rssMb: 0, cpuPercent: 0 };

  if (isWin) {
    try {
      const pidList = pids.join(',');
      const out = execSync(
        `powershell -NoProfile -Command "Get-Process -Id ${pidList} -ErrorAction SilentlyContinue | Measure-Object -Property WorkingSet64 -Sum | Select-Object -ExpandProperty Sum"`,
        { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] },
      );
      const totalBytes = Number(out.trim()) || 0;
      return {
        rssMb: totalBytes / (1024 * 1024),
        cpuPercent: 0, // Instant CPU requires delta calculation on Windows
      };
    } catch {
      return { rssMb: 0, cpuPercent: 0 };
    }
  } else {
    try {
      const pidList = pids.join(',');
      const out = execSync(`ps -p ${pidList} -o rss,%cpu --no-headers`, {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'ignore'],
      });
      let totalRssKb = 0;
      let totalCpu = 0;
      for (const line of out.trim().split('\n')) {
        const parts = line.trim().split(/\s+/);
        totalRssKb += Number(parts[0]) || 0;
        totalCpu += Number(parts[1]) || 0;
      }
      return {
        rssMb: totalRssKb / 1024,
        cpuPercent: totalCpu,
      };
    } catch {
      return { rssMb: 0, cpuPercent: 0 };
    }
  }
}

/**
 * Benchmark a single desktop shell.
 */
async function benchmarkShell(shellType, duration) {
  console.log(`\n======================================================`);
  console.log(`🚀 Benchmarking ${shellType.toUpperCase()} Shell (${duration}s test)`);
  console.log(`======================================================`);

  const t0 = performance.now();
  let child;
  const env = {
    ...process.env,
    HORRIBLE_DEV_HOST: '127.0.0.1',
    ELECTRON_DISABLE_SANDBOX: '1',
  };

  child = spawn(process.execPath, ['scripts/dev-desktop.mjs', '--shell', shellType], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env,
    detached: !isWin,
  });

  // Resume child stdio streams so OS buffers never fill and block
  child.stdout?.resume();
  child.stderr?.resume();

  const rootPid = child.pid;
  console.log(`Spawned runner PID: ${rootPid}`);

  // Wait for frontend and backend readiness
  process.stdout.write('Waiting for backend readiness (http://127.0.0.1:8000/api/health)... ');
  const backendLatency = await pollUrl('http://127.0.0.1:8000/api/health', 45000);
  console.log(backendLatency ? `${backendLatency.toFixed(0)} ms ✅` : 'TIMED OUT ❌');

  process.stdout.write('Waiting for frontend dev server (http://localhost:5173)... ');
  const frontendLatency = await pollUrl('http://localhost:5173', 45000);
  console.log(frontendLatency ? `${frontendLatency.toFixed(0)} ms ✅` : 'TIMED OUT ❌');

  const bootTotalLatency = performance.now() - t0;
  console.log(`Total Startup to Interactive: ${bootTotalLatency.toFixed(0)} ms`);

  // Allow window to mount and settle
  console.log(`Settling for 5 seconds...`);
  await new Promise((r) => setTimeout(r, 5000));

  // Collect samples over the specified duration
  const pids = getProcessTreePids(rootPid);
  console.log(`Tracked process tree (${pids.length} processes): PIDs [${pids.join(', ')}]`);

  const samples = [];
  const startTime = Date.now();
  const sampleIntervalMs = 2000;
  const numSamples = Math.floor((duration * 1000) / sampleIntervalMs);

  console.log(`Collecting ${numSamples} metrics samples over ${duration}s...`);

  for (let i = 0; i < numSamples; i++) {
    const currentPids = getProcessTreePids(rootPid);
    const metrics = sampleProcessTreeMetrics(currentPids);
    const elapsedSec = (Date.now() - startTime) / 1000;
    samples.push({
      elapsedSec,
      rssMb: metrics.rssMb,
      cpuPercent: metrics.cpuPercent,
      processCount: currentPids.length,
    });
    process.stdout.write(
      `\r  [${elapsedSec.toFixed(1)}s / ${duration}s] Total RAM: ${metrics.rssMb.toFixed(1)} MB | CPU: ${metrics.cpuPercent.toFixed(1)}% `,
    );
    await new Promise((r) => setTimeout(r, sampleIntervalMs));
  }
  console.log('\nSampling complete.');

  // Terminate runner & tree
  console.log('Shutting down benchmark processes...');
  const cleanupPids = getProcessTreePids(rootPid);
  if (isWin) {
    try {
      execSync(`taskkill /PID ${rootPid} /T /F`, { stdio: 'ignore' });
    } catch {}
  } else {
    try {
      process.kill(-rootPid, 'SIGTERM');
    } catch {}
    for (const pid of cleanupPids) {
      try {
        process.kill(pid, 'SIGTERM');
      } catch {}
    }
    // Final force kill after short delay
    await new Promise((r) => setTimeout(r, 1000));
    try {
      process.kill(-rootPid, 'SIGKILL');
    } catch {}
    for (const pid of cleanupPids) {
      try {
        process.kill(pid, 'SIGKILL');
      } catch {}
    }
  }

  // Compute analytics
  const rssValues = samples.map((s) => s.rssMb).filter((v) => v > 0);
  const cpuValues = samples.map((s) => s.cpuPercent);

  const initialRss = rssValues[0] || 0;
  const finalRss = rssValues[rssValues.length - 1] || 0;
  const peakRss = Math.max(...(rssValues.length ? rssValues : [0]));
  const minRss = Math.min(...(rssValues.length ? rssValues : [0]));
  const netGrowthRss = finalRss - initialRss;
  const growthRatePerMin = (netGrowthRss / (duration / 60)).toFixed(2);

  const avgCpu = cpuValues.length ? cpuValues.reduce((a, b) => a + b, 0) / cpuValues.length : 0;
  const peakCpu = Math.max(...(cpuValues.length ? cpuValues : [0]));

  const summary = {
    shell: shellType,
    platform: process.platform,
    testDurationSec: duration,
    bootTotalMs: Number(bootTotalLatency.toFixed(0)),
    backendLatencyMs: backendLatency ? Number(backendLatency.toFixed(0)) : null,
    frontendLatencyMs: frontendLatency ? Number(frontendLatency.toFixed(0)) : null,
    initialMemoryMb: Number(initialRss.toFixed(1)),
    finalMemoryMb: Number(finalRss.toFixed(1)),
    peakMemoryMb: Number(peakRss.toFixed(1)),
    netMemoryGrowthMb: Number(netGrowthRss.toFixed(1)),
    memoryGrowthPerMinMb: Number(growthRatePerMin),
    avgCpuPercent: Number(avgCpu.toFixed(1)),
    peakCpuPercent: Number(peakCpu.toFixed(1)),
    samples,
  };

  return summary;
}

/**
 * Format markdown table for comparison.
 */
function printComparisonTable(results) {
  const [r1, r2] = results;
  const delta = (v1, v2, suffix = '', inverseGood = false) => {
    if (v1 == null || v2 == null || v1 === 0) return 'N/A';
    const pct = (((v2 - v1) / v1) * 100).toFixed(1);
    const sign = Number(pct) > 0 ? `+${pct}%` : `${pct}%`;
    const isGood = inverseGood ? Number(pct) > 0 : Number(pct) < 0;
    const badge = isGood ? '✅' : '⚠️';
    return `${sign} ${badge}`;
  };

  console.log(`\n======================================================`);
  console.log(`📊 DESKTOP SHELL PERFORMANCE BENCHMARK RESULTS (${process.platform})`);
  console.log(`======================================================\n`);

  console.log(
    `| Metric | ${r1.shell.toUpperCase()} | ${r2.shell.toUpperCase()} | Delta (${r2.shell} vs ${r1.shell}) |`,
  );
  console.log(`| :--- | :--- | :--- | :--- |`);
  console.log(
    `| **Total Startup Latency** | ${r1.bootTotalMs} ms | ${r2.bootTotalMs} ms | ${delta(r1.bootTotalMs, r2.bootTotalMs)} |`,
  );
  console.log(
    `| **Backend Health Latency** | ${r1.backendLatencyMs ?? 'N/A'} ms | ${r2.backendLatencyMs ?? 'N/A'} ms | ${delta(r1.backendLatencyMs, r2.backendLatencyMs)} |`,
  );
  console.log(
    `| **Initial Process Memory (RSS)** | ${r1.initialMemoryMb} MB | ${r2.initialMemoryMb} MB | ${delta(r1.initialMemoryMb, r2.initialMemoryMb)} |`,
  );
  console.log(
    `| **Final Process Memory (RSS)** | ${r1.finalMemoryMb} MB | ${r2.finalMemoryMb} MB | ${delta(r1.finalMemoryMb, r2.finalMemoryMb)} |`,
  );
  console.log(
    `| **Peak Memory Consumption** | ${r1.peakMemoryMb} MB | ${r2.peakMemoryMb} MB | ${delta(r1.peakMemoryMb, r2.peakMemoryMb)} |`,
  );
  console.log(
    `| **Memory Growth Rate** | ${r1.memoryGrowthPerMinMb} MB/min | ${r2.memoryGrowthPerMinMb} MB/min | ${delta(r1.memoryGrowthPerMinMb, r2.memoryGrowthPerMinMb)} |`,
  );
  console.log(
    `| **Average CPU Utilization** | ${r1.avgCpuPercent}% | ${r2.avgCpuPercent}% | ${delta(r1.avgCpuPercent, r2.avgCpuPercent)} |`,
  );
  console.log(
    `| **Peak CPU Utilization** | ${r1.peakCpuPercent}% | ${r2.peakCpuPercent}% | ${delta(r1.peakCpuPercent, r2.peakCpuPercent)} |`,
  );
  console.log('\n');
}

async function main() {
  const outDir = path.resolve('benchmark-results');
  if (!fs.existsSync(outDir)) {
    fs.mkdirSync(outDir, { recursive: true });
  }

  const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
  const outFile = path.join(outDir, `benchmark-${process.platform}-${timestamp}.json`);

  if (compare) {
    console.log(`Running comparative benchmark: Tauri vs Electron on ${process.platform}...`);
    const tauriRes = await benchmarkShell('tauri', durationSec);
    console.log('Cooling down for 5 seconds before second run...');
    await new Promise((r) => setTimeout(r, 5000));
    const electronRes = await benchmarkShell('electron', durationSec);

    printComparisonTable([tauriRes, electronRes]);

    fs.writeFileSync(outFile, JSON.stringify({ tauri: tauriRes, electron: electronRes }, null, 2));
    console.log(`Saved detailed benchmark JSON: ${outFile}`);
  } else {
    const res = await benchmarkShell(targetShell, durationSec);
    console.log(`\nSummary for ${targetShell.toUpperCase()}:`);
    console.log(`  Boot Latency: ${res.bootTotalMs} ms`);
    console.log(`  Initial Memory: ${res.initialMemoryMb} MB`);
    console.log(`  Final Memory: ${res.finalMemoryMb} MB`);
    console.log(`  Memory Growth: ${res.memoryGrowthPerMinMb} MB/min`);
    console.log(`  Average CPU: ${res.avgCpuPercent}%`);

    fs.writeFileSync(outFile, JSON.stringify(res, null, 2));
    console.log(`Saved detailed benchmark JSON: ${outFile}`);
  }
  process.exit(0);
}

main().catch((err) => {
  console.error('Benchmark failed:', err);
  process.exit(1);
});
