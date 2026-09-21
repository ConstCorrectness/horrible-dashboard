import { useState, useEffect } from 'react';
import { getMapCubes, formatBytes } from '@horrible/core';

export interface InstantDeployModalProps {
  room: string;
  map: string;
  callsign: string;
  onCallsignChange: (name: string) => void;
  onDeploy: (room: string, map: string) => void;
  onCancel: () => void;
}

export function InstantDeployModal({
  room,
  map,
  callsign,
  onCallsignChange,
  onDeploy,
  onCancel,
}: InstantDeployModalProps) {
  const [downloadProgress, setDownloadProgress] = useState<{ loaded: number; total: number | null }>({
    loaded: 0,
    total: null,
  });
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function preload() {
      try {
        setError(null);
        await getMapCubes(map, (loaded, total) => {
          if (!cancelled) setDownloadProgress({ loaded, total });
        });
        if (!cancelled) setReady(true);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    }
    void preload();
    return () => {
      cancelled = true;
    };
  }, [map]);

  const handleDeployClick = () => {
    // Unlock Web Audio context on user click
    try {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (AudioCtx) {
        const ctx = new AudioCtx();
        if (ctx.state === 'suspended') {
          void ctx.resume();
        }
      }
    } catch {
      // AudioContext init error ignored
    }
    onDeploy(room, map);
  };

  const pct =
    downloadProgress.total && downloadProgress.total > 0
      ? Math.min(100, Math.round((downloadProgress.loaded / downloadProgress.total) * 100))
      : ready
      ? 100
      : 50;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(5, 8, 14, 0.85)',
        backdropFilter: 'blur(8px)',
        zIndex: 9000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1.5rem',
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 520,
          backgroundColor: 'rgba(15, 23, 42, 0.95)',
          border: '1px solid #334155',
          borderRadius: 12,
          padding: '2rem',
          boxShadow: '0 25px 60px rgba(0, 0, 0, 0.7)',
          display: 'flex',
          flexDirection: 'column',
          gap: '1.4rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <span style={{ fontSize: '0.75rem', fontWeight: 700, color: '#38bdf8', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
              Direct Match Deployment
            </span>
            <h2 style={{ fontSize: '1.4rem', fontWeight: 800, color: '#f8fafc', marginTop: '0.2rem' }}>
              Join Combat Zone
            </h2>
          </div>
          <span
            style={{
              fontSize: '0.75rem',
              fontWeight: 700,
              padding: '0.3rem 0.6rem',
              borderRadius: 4,
              backgroundColor: '#1e293b',
              border: '1px solid #475569',
              color: '#94a3b8',
            }}
          >
            {map.toUpperCase()}
          </span>
        </div>

        <div
          style={{
            backgroundColor: '#0b0f17',
            border: '1px solid #1e293b',
            borderRadius: 8,
            padding: '1rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.6rem',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.82rem', color: '#cbd5e1' }}>
            <span>Map Geometry Preload</span>
            <span style={{ fontFamily: 'monospace', color: '#38bdf8' }}>
              {ready ? 'READY (100%)' : `${formatBytes(downloadProgress.loaded, downloadProgress.total)} (${pct}%)`}
            </span>
          </div>

          <div
            style={{
              height: 6,
              backgroundColor: '#1e293b',
              borderRadius: 3,
              overflow: 'hidden',
              position: 'relative',
            }}
          >
            <div
              style={{
                width: `${pct}%`,
                height: '100%',
                backgroundColor: ready ? '#10b981' : '#38bdf8',
                transition: 'width 0.2s ease',
              }}
            />
          </div>
          {error && <span style={{ fontSize: '0.75rem', color: '#ef4444' }}>{error}</span>}
        </div>

        <div>
          <label
            htmlFor="callsign-input"
            style={{
              display: 'block',
              fontSize: '0.82rem',
              fontWeight: 600,
              color: '#94a3b8',
              marginBottom: '0.4rem',
            }}
          >
            Operative Callsign:
          </label>
          <input
            id="callsign-input"
            type="text"
            maxLength={16}
            value={callsign}
            onChange={(e) => onCallsignChange(e.target.value)}
            placeholder="Enter Callsign..."
            style={{
              width: '100%',
              backgroundColor: '#090d16',
              border: '1px solid #334155',
              borderRadius: 6,
              color: '#f8fafc',
              padding: '0.7rem 1rem',
              fontSize: '0.95rem',
              fontWeight: 600,
              outline: 'none',
              boxSizing: 'border-box',
            }}
          />
        </div>

        <div style={{ display: 'flex', gap: '0.8rem', marginTop: '0.5rem' }}>
          <button
            type="button"
            onClick={onCancel}
            style={{
              flex: 1,
              backgroundColor: '#1e293b',
              border: '1px solid #334155',
              color: '#cbd5e1',
              borderRadius: 6,
              padding: '0.8rem 1rem',
              fontSize: '0.9rem',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Browse Servers
          </button>
          <button
            type="button"
            disabled={!ready && !error}
            onClick={handleDeployClick}
            style={{
              flex: 2,
              backgroundColor: ready ? '#0284c7' : '#0369a1',
              color: '#ffffff',
              border: 'none',
              borderRadius: 6,
              padding: '0.8rem 1rem',
              fontSize: '0.95rem',
              fontWeight: 800,
              letterSpacing: '0.05em',
              cursor: ready ? 'pointer' : 'default',
              boxShadow: ready ? '0 4px 14px rgba(2, 132, 199, 0.4)' : 'none',
              transition: 'all 0.15s ease',
            }}
          >
            {ready ? '⚡ DEPLOY TO COMBAT' : 'LOADING MAP...'}
          </button>
        </div>
      </div>
    </div>
  );
}
