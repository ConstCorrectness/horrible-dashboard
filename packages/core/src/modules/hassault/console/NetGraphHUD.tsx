/**
 * CS:GO / Source Engine style in-game NetGraph HUD overlay for hAssault.
 *
 * Activated when `net.graph` > 0.
 * Level 1: fps, frame-time p99, ping
 * Level 2: + CPU / GPU frame cost, draw calls, triangles, tick and snapshot
 *          rate, in/out KB/s, interpolation delay
 * Level 3: + loss, jitter, simulated lag/loss
 *
 * Every number is measured (`perf-stats.ts`, `net-stats.ts`). These rows used
 * to be string literals — `tick: 20.0` survived the server going to 60 Hz — and
 * a readout that cannot move is worse than none. What has not been measured
 * yet shows as `—`, never as a plausible default.
 *
 * Polled twice a second from the pane's `PerfProbe` rather than pushed: the
 * pane re-rendering at display rate to update this would cost more than
 * everything here measures.
 */

import React, { useEffect, useState } from 'react';

import type { PerfProbe } from '../perf-stats';
import { consoleRegistry } from './registry';

interface NetGraphHUDProps {
  rttMs: number;
  level?: number;
  /** The pane's numbers. Absent, only ping is shown. */
  probe?: React.RefObject<PerfProbe | null>;
  style?: React.CSSProperties;
}

interface Readout {
  fps: number | null;
  frameP99: number | null;
  cpuP50: number | null;
  cpuP99: number | null;
  gpuP50: number | null;
  gpuTimed: boolean;
  calls: number;
  tris: number;
  tickHz: number | null;
  snapHz: number | null;
  inKBs: number | null;
  outKBs: number | null;
  interpMs: number | null;
  lossPct: number | null;
  jitterMs: number | null;
}

const EMPTY: Readout = {
  fps: null,
  frameP99: null,
  cpuP50: null,
  cpuP99: null,
  gpuP50: null,
  gpuTimed: false,
  calls: 0,
  tris: 0,
  tickHz: null,
  snapHz: null,
  inKBs: null,
  outKBs: null,
  interpMs: null,
  lossPct: null,
  jitterMs: null,
};

const POLL_MS = 500;

function read(probe: PerfProbe | null): Readout {
  if (!probe) return EMPTY;
  const mean = probe.frames.mean();
  const net = probe.net();
  return {
    fps: mean && mean > 0 ? 1000 / mean : null,
    frameP99: probe.frames.percentile(99),
    cpuP50: probe.cpu.percentile(50),
    cpuP99: probe.cpu.percentile(99),
    gpuP50: probe.frames.gpuPercentile(50),
    gpuTimed: probe.gpuTimed,
    calls: probe.drawCalls(),
    tris: probe.triangles(),
    tickHz: net?.serverTickHz ?? null,
    snapHz: net?.snapshotHz ?? null,
    inKBs: net?.inBytesPerSec != null ? net.inBytesPerSec / 1024 : null,
    outKBs: net?.outBytesPerSec != null ? net.outBytesPerSec / 1024 : null,
    interpMs: net ? probe.interpDelayMs() : null,
    lossPct: net?.lossPct ?? null,
    jitterMs: net?.jitterMs ?? null,
  };
}

/** One decimal, or `—` for a number that has not been measured. */
function fmt(v: number | null, digits = 1): string {
  return v == null || !Number.isFinite(v) ? '—' : v.toFixed(digits);
}

export function NetGraphHUD({ rttMs, level: propLevel, probe, style }: NetGraphHUDProps) {
  const [level, setLevel] = useState<number>(propLevel ?? consoleRegistry.getNumber('net.graph'));
  const [simLag, setSimLag] = useState<number>(consoleRegistry.getNumber('net.simulate_lag'));
  const [simLoss, setSimLoss] = useState<number>(consoleRegistry.getNumber('net.simulate_loss'));
  const [r, setR] = useState<Readout>(EMPTY);

  useEffect(() => {
    return consoleRegistry.subscribe((name, val) => {
      if (name === 'net.graph') setLevel(Number(val) || 0);
      if (name === 'net.simulate_lag') setSimLag(Number(val) || 0);
      if (name === 'net.simulate_loss') setSimLoss(Number(val) || 0);
    });
  }, []);

  useEffect(() => {
    if (level <= 0) return;
    const tick = () => setR(read(probe?.current ?? null));
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => clearInterval(id);
  }, [level, probe]);

  if (level <= 0) return null;

  const totalPing = Math.round(rttMs + simLag);
  const pingColor = totalPing < 50 ? '#4ade80' : totalPing < 100 ? '#facc15' : '#f87171';
  // Judged against the 144 Hz budget the game is tuned for, not against 60.
  const fpsColor =
    r.fps == null ? '#94a3b8' : r.fps >= 140 ? '#4ade80' : r.fps >= 60 ? '#facc15' : '#f87171';
  const lossColor = (r.lossPct ?? 0) > 0 ? '#f87171' : '#4ade80';

  return (
    <div
      style={{
        position: 'absolute',
        bottom: 24,
        right: 24,
        zIndex: 4,
        pointerEvents: 'none',
        userSelect: 'none',
        fontFamily: 'Consolas, "Roboto Mono", "Courier New", monospace',
        fontSize: '0.78rem',
        lineHeight: 1.35,
        color: '#e2e8f0',
        background: 'rgba(10, 14, 20, 0.82)',
        border: '1px solid rgba(255, 255, 255, 0.12)',
        borderRadius: 4,
        padding: '6px 10px',
        boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
        letterSpacing: '0.04em',
        ...style,
      }}
    >
      <div style={{ display: 'flex', gap: '14px', alignItems: 'center' }}>
        <span>
          fps: <strong style={{ color: fpsColor }}>{fmt(r.fps, 0)}</strong>{' '}
          <span style={{ color: '#94a3b8', fontSize: '0.72rem' }}>(p99 {fmt(r.frameP99)}ms)</span>
        </span>
        <span>
          ping: <strong style={{ color: pingColor }}>{totalPing} ms</strong>
          {simLag > 0 && <span style={{ color: '#facc15', fontSize: '0.7rem' }}> (+{simLag})</span>}
        </span>
      </div>

      {level >= 2 && (
        <>
          <div style={rowStyle}>
            <span>
              cpu: <strong>{fmt(r.cpuP50)}</strong>/<strong>{fmt(r.cpuP99)}ms</strong>
            </span>
            <span>
              gpu: <strong>{r.gpuTimed ? `${fmt(r.gpuP50)}ms` : 'n/a'}</strong>
            </span>
            <span>
              calls: <strong>{r.calls}</strong>
            </span>
            <span>
              tris: <strong>{Math.round(r.tris / 1000)}k</strong>
            </span>
          </div>
          <div style={rowStyle}>
            <span>
              tick: <strong>{fmt(r.tickHz)}</strong>
            </span>
            <span>
              snap: <strong>{fmt(r.snapHz)}/s</strong>
            </span>
            <span>
              in: <strong>{fmt(r.inKBs)} KB/s</strong>
            </span>
            <span>
              out: <strong>{fmt(r.outKBs)} KB/s</strong>
            </span>
            <span>
              interp: <strong>{fmt(r.interpMs, 0)}ms</strong>
            </span>
          </div>
        </>
      )}

      {level >= 3 && (
        <div style={{ ...rowStyle, fontSize: '0.72rem' }}>
          <span>
            loss: <strong style={{ color: lossColor }}>{fmt(r.lossPct)}%</strong>
          </span>
          <span>
            jitter: <strong>{fmt(r.jitterMs)}ms</strong>
          </span>
          {(simLag > 0 || simLoss > 0) && (
            <span style={{ color: '#facc15' }}>
              sim: +{simLag}ms / {Math.round(simLoss * 100)}%
            </span>
          )}
        </div>
      )}
    </div>
  );
}

const rowStyle: React.CSSProperties = {
  display: 'flex',
  gap: '14px',
  marginTop: '3px',
  color: '#cbd5e1',
  fontSize: '0.74rem',
};
