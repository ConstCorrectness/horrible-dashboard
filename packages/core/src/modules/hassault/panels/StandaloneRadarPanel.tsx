import { useEffect, useMemo, useRef, useState } from 'react';

import { registry } from '../../../registry';
import { getMapCubes, getMapInfo } from '../api';
import { World } from '../world';
import { getLatestMatchTelemetry, onMatchTelemetry, type MatchTelemetry } from '../workspace-bus';

export function StandaloneRadarPanel() {
  const [telemetry, setTelemetry] = useState<MatchTelemetry | null>(getLatestMatchTelemetry);
  const [world, setWorld] = useState<World | null>(null);
  const [rotateWithPlayer, setRotateWithPlayer] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [loadedMap, setLoadedMap] = useState<string>('');

  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Subscribe to live telemetry
  useEffect(() => {
    return onMatchTelemetry((data) => {
      setTelemetry(data);
    });
  }, []);

  // Fetch map cubes whenever telemetry changes map
  const activeMap = telemetry?.mapName || 'ac_desert';
  useEffect(() => {
    let cancelled = false;
    if (activeMap === loadedMap && world) return;

    async function loadMap() {
      try {
        const info = await getMapInfo(activeMap);
        if (cancelled) return;
        const cubes = await getMapCubes(activeMap);
        if (cancelled) return;
        const w = new World(info, cubes);
        setWorld(w);
        setLoadedMap(activeMap);
      } catch {
        // Fallback or offline map
      }
    }

    void loadMap();
    return () => {
      cancelled = true;
    };
  }, [activeMap, loadedMap, world]);

  // Pre-render map floor plan offscreen
  const mapCanvas = useMemo(() => {
    if (!world || typeof document === 'undefined') return null;
    const scale = 2;
    const size = Math.ceil(world.ssize * scale);
    const cvs = document.createElement('canvas');
    cvs.width = size;
    cvs.height = size;
    const ctx = cvs.getContext('2d');
    if (!ctx) return null;

    ctx.fillStyle = 'rgba(140, 170, 210, 0.2)';
    for (let y = 0; y < world.ssize; y++) {
      for (let x = 0; x < world.ssize; x++) {
        if (!world.isSolid(x, y)) {
          ctx.fillRect(x * scale, y * scale, scale, scale);
        }
      }
    }
    return cvs;
  }, [world]);

  // Render tactical radar loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = canvas.clientWidth || 320;
    const height = canvas.clientHeight || 320;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
      canvas.width = width * dpr;
      canvas.height = height * dpr;
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // Background
    ctx.fillStyle = 'rgba(13, 17, 23, 0.95)';
    ctx.fillRect(0, 0, width, height);

    const cx = width / 2;
    const cy = height / 2;

    const player = telemetry?.player;
    const px = player?.x ?? (world ? world.ssize / 2 : 64);
    const py = player?.y ?? (world ? world.ssize / 2 : 64);
    const yaw = player?.yaw ?? 0;

    const baseSpan = 110;
    const span = baseSpan / zoom;
    const pxPerCube = Math.min(width, height) / span;

    ctx.save();

    // Clipping circle
    const rad = Math.min(width, height) / 2 - 8;
    ctx.beginPath();
    ctx.arc(cx, cy, rad, 0, Math.PI * 2);
    ctx.clip();

    ctx.fillStyle = 'rgba(8, 12, 18, 0.85)';
    ctx.fill();

    // World transform
    ctx.save();
    ctx.translate(cx, cy);
    if (rotateWithPlayer) {
      ctx.rotate(-yaw - Math.PI / 2);
    }
    ctx.scale(pxPerCube, pxPerCube);
    ctx.translate(-px, -py);

    // Blit pre-rendered map
    if (mapCanvas && world) {
      ctx.drawImage(mapCanvas, 0, 0, world.ssize, world.ssize);
    }

    // Draw grid rings
    ctx.restore();

    ctx.strokeStyle = 'rgba(56, 189, 248, 0.15)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, rad * 0.33, 0, Math.PI * 2);
    ctx.arc(cx, cy, rad * 0.66, 0, Math.PI * 2);
    ctx.stroke();

    // Draw Crosshairs
    ctx.beginPath();
    ctx.moveTo(cx - rad, cy);
    ctx.lineTo(cx + rad, cy);
    ctx.moveTo(cx, cy - rad);
    ctx.lineTo(cx, cy + rad);
    ctx.stroke();

    // Draw other players / roster
    if (telemetry?.roster) {
      for (const p of telemetry.roster) {
        // Compute delta
        const dx = p.x - px;
        const dy = p.y - py;
        let screenX = cx;
        let screenY = cy;

        if (rotateWithPlayer) {
          const cos = Math.cos(-yaw - Math.PI / 2);
          const sin = Math.sin(-yaw - Math.PI / 2);
          screenX = cx + (dx * cos - dy * sin) * pxPerCube;
          screenY = cy + (dx * sin + dy * cos) * pxPerCube;
        } else {
          screenX = cx + dx * pxPerCube;
          screenY = cy + dy * pxPerCube;
        }

        // Draw player dot
        const isSpotted = telemetry.spotted.includes(p.id);
        const color =
          p.team === 0
            ? 'rgb(96, 165, 250)'
            : isSpotted
            ? 'rgb(239, 68, 68)'
            : 'rgba(239, 68, 68, 0.3)';

        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(screenX, screenY, 4, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Draw local player arrow at center
    ctx.fillStyle = 'rgb(34, 197, 94)';
    ctx.beginPath();
    if (rotateWithPlayer) {
      // Facing up
      ctx.moveTo(cx, cy - 8);
      ctx.lineTo(cx - 5, cy + 5);
      ctx.lineTo(cx, cy + 2);
      ctx.lineTo(cx + 5, cy + 5);
    } else {
      const cos = Math.cos(yaw);
      const sin = Math.sin(yaw);
      ctx.moveTo(cx + cos * 8, cy + sin * 8);
      ctx.lineTo(cx + Math.cos(yaw + 2.5) * 6, cy + Math.sin(yaw + 2.5) * 6);
      ctx.lineTo(cx, cy);
      ctx.lineTo(cx + Math.cos(yaw - 2.5) * 6, cy + Math.sin(yaw - 2.5) * 6);
    }
    ctx.closePath();
    ctx.fill();

    // Radar border
    ctx.restore();
    ctx.strokeStyle = 'rgb(56, 189, 248)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, rad, 0, Math.PI * 2);
    ctx.stroke();
  }, [telemetry, world, mapCanvas, rotateWithPlayer, zoom]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: 'rgb(13, 17, 23)',
        color: 'rgb(241, 245, 249)',
      }}
    >
      {/* Top Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0.6rem 0.8rem',
          borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
          background: 'rgba(22, 27, 34, 0.95)',
          fontSize: '0.75rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontWeight: 800, color: 'rgb(56, 189, 248)' }}>⦿ RADAR</span>
          <span style={{ color: 'rgb(148, 163, 184)' }}>[{activeMap}]</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <button
            type="button"
            className={rotateWithPlayer ? 'games-play-btn' : 'games-ghost-btn'}
            style={{ fontSize: '0.68rem', padding: '2px 6px' }}
            onClick={() => setRotateWithPlayer(!rotateWithPlayer)}
            title="Toggle Rotate With Player vs North-Up"
          >
            {rotateWithPlayer ? 'Rotate: Player' : 'Rotate: North'}
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.68rem', padding: '2px 6px' }}
            onClick={() => setZoom((z) => Math.min(2.5, z + 0.25))}
            title="Zoom In"
          >
            +
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.68rem', padding: '2px 6px' }}
            onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}
            title="Zoom Out"
          >
            -
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.68rem', padding: '2px 6px' }}
            onClick={() => registry.openPanel('hassault.play')}
          >
            Play
          </button>
        </div>
      </div>

      {/* Main Canvas Viewport */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />

        {/* Position Overlay */}
        {telemetry?.player && (
          <div
            style={{
              position: 'absolute',
              bottom: 8,
              left: 8,
              background: 'rgba(13, 17, 23, 0.85)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              borderRadius: 4,
              padding: '2px 6px',
              fontSize: '0.65rem',
              fontFamily: 'monospace',
              color: 'rgb(148, 163, 184)',
            }}
          >
            X: {telemetry.player.x.toFixed(1)} Y: {telemetry.player.y.toFixed(1)} Yaw:{' '}
            {((telemetry.player.yaw * 180) / Math.PI).toFixed(0)}°
          </div>
        )}
      </div>
    </div>
  );
}
