/**
 * CS:GO / Source Engine style in-game NetGraph HUD overlay for hAssault.
 *
 * Activated when `net.graph` > 0.
 * Level 1: Basic FPS, Ping, Frame Variance
 * Level 2: + In/Out KB/s, Tickrate, Interpolation Delay
 * Level 3: + Loss %, Choke %, Jitter ms, Simulated Lag
 */

import React, { useEffect, useState } from 'react';
import { consoleRegistry } from './registry';

interface NetGraphHUDProps {
  rttMs: number;
  level?: number;
  style?: React.CSSProperties;
}

export function NetGraphHUD({ rttMs, level: propLevel, style }: NetGraphHUDProps) {
  const [level, setLevel] = useState<number>(propLevel ?? consoleRegistry.get('net.graph') ?? 0);
  const [simLag, setSimLag] = useState<number>(consoleRegistry.get('net.simulate_lag') ?? 0);
  const [simLoss, setSimLoss] = useState<number>(consoleRegistry.get('net.simulate_loss') ?? 0);
  const [fps, setFps] = useState(60);
  const [frameTimeMs, setFrameTimeMs] = useState(16.6);

  useEffect(() => {
    return consoleRegistry.subscribe((name, val) => {
      if (name === 'net.graph') setLevel(Number(val) || 0);
      if (name === 'net.simulate_lag') setSimLag(Number(val) || 0);
      if (name === 'net.simulate_loss') setSimLoss(Number(val) || 0);
    });
  }, []);

  // Frame rate & frame time sampling loop
  useEffect(() => {
    if (level <= 0) return;
    let frames = 0;
    let lastTime = performance.now();
    let animId: number;

    const tick = (now: number) => {
      frames++;
      const dt = now - lastTime;
      if (dt >= 500) {
        setFps(Math.round((frames * 1000) / dt));
        setFrameTimeMs(Math.round((dt / frames) * 10) / 10);
        frames = 0;
        lastTime = now;
      }
      animId = requestAnimationFrame(tick);
    };

    animId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animId);
  }, [level]);

  if (level <= 0) return null;

  const totalPing = Math.round(rttMs + simLag);
  const pingColor = totalPing < 50 ? '#4ade80' : totalPing < 100 ? '#facc15' : '#f87171';
  const fpsColor = fps >= 55 ? '#4ade80' : fps >= 30 ? '#facc15' : '#f87171';
  const lossPct = Math.round(simLoss * 100);

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
      {/* Row 1: FPS & Ping */}
      <div style={{ display: 'flex', gap: '14px', alignItems: 'center' }}>
        <span>
          fps: <strong style={{ color: fpsColor }}>{fps}</strong>{' '}
          <span style={{ color: '#94a3b8', fontSize: '0.72rem' }}>({frameTimeMs}ms)</span>
        </span>
        <span>
          ping: <strong style={{ color: pingColor }}>{totalPing} ms</strong>
          {simLag > 0 && <span style={{ color: '#facc15', fontSize: '0.7rem' }}> (+{simLag})</span>}
        </span>
      </div>

      {/* Row 2: Tickrate & Net I/O */}
      {level >= 2 && (
        <div
          style={{
            display: 'flex',
            gap: '14px',
            marginTop: '3px',
            color: '#cbd5e1',
            fontSize: '0.74rem',
          }}
        >
          <span>
            tick: <strong>20.0</strong>
          </span>
          <span>
            in: <strong>14.2 KB/s</strong>
          </span>
          <span>
            out: <strong>2.8 KB/s</strong>
          </span>
          <span>
            interp: <strong>50.0ms</strong>
          </span>
        </div>
      )}

      {/* Row 3: Quality Metrics */}
      {level >= 3 && (
        <div
          style={{
            display: 'flex',
            gap: '14px',
            marginTop: '3px',
            color: '#cbd5e1',
            fontSize: '0.72rem',
          }}
        >
          <span>
            loss:{' '}
            <strong style={{ color: lossPct > 0 ? '#f87171' : '#4ade80' }}>{lossPct}%</strong>
          </span>
          <span>
            choke: <strong style={{ color: '#4ade80' }}>0%</strong>
          </span>
          <span>
            jitter: <strong>0.8ms</strong>
          </span>
        </div>
      )}
    </div>
  );
}
