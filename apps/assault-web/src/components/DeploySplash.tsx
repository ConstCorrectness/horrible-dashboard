import { useEffect, useState } from 'react';
import { DEFAULT_CONTROLS, formatBytes, getMapCubes, keyLabel } from '@horrible/core';
import { accountName, useAuth } from '../auth';
import { mapEntry } from '../maps';
import { MapBackdrop } from './MapBackdrop';

export interface DeploySplashProps {
  /** Null opens a new room on `map`. */
  room: string | null;
  map: string;
  callsign: string;
  onCallsignChange: (name: string) => void;
  onDeploy: () => void;
  onCancel: () => void;
}

/** Fire and aim are the mouse and never rebindable; everything else is the shipped default. */
const KEYS: { label: string; keys: string[] }[] = [
  { label: 'Fire', keys: ['Mouse 1'] },
  { label: 'Aim', keys: ['Mouse 2'] },
  { label: 'Move', keys: ['forward', 'left', 'back', 'right'].map((a) => key(a)) },
  { label: 'Jump', keys: [key('jump')] },
  { label: 'Crouch', keys: [key('crouch')] },
  { label: 'Sprint', keys: [key('sprint')] },
  { label: 'Reload', keys: [key('reload')] },
  { label: 'Use', keys: [key('use')] },
  { label: 'Buy menu', keys: [key('buy')] },
  { label: 'Throw', keys: [key('throw')] },
  { label: 'Ping', keys: [key('ping')] },
  { label: 'Scores', keys: [key('scores')] },
  { label: 'Voice', keys: [key('voice')] },
  { label: 'Chat', keys: [key('chatAll')] },
  { label: 'Menu', keys: ['Esc'] },
];

function key(action: string): string {
  const code = (DEFAULT_CONTROLS as Record<string, string[]>)[action]?.[0];
  return code ? keyLabel(code) : '—';
}

/**
 * Between choosing a match and being in it: the map, the download, your name,
 * and the keys — over the map itself.
 *
 * The map's geometry downloads here, so the bar is the real transfer (bytes from
 * `getMapCubes`), and Deploy is the click a browser needs anyway before it will
 * start audio or lock the pointer.
 */
export function DeploySplash({
  room,
  map,
  callsign,
  onCallsignChange,
  onDeploy,
  onCancel,
}: DeploySplashProps) {
  const { account } = useAuth();
  const handle = accountName(account);
  const entry = mapEntry(map);
  const [progress, setProgress] = useState<{ loaded: number; total: number | null }>({
    loaded: 0,
    total: null,
  });
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setReady(false);
    setError(null);
    getMapCubes(map, (loaded, total) => {
      if (!cancelled) setProgress({ loaded, total });
    })
      .then(() => !cancelled && setReady(true))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [map]);

  const pct = ready
    ? 100
    : progress.total && progress.total > 0
      ? Math.min(99, Math.round((progress.loaded / progress.total) * 100))
      : 0;

  const deploy = () => {
    // Resume audio inside the click: browsers refuse to start it any other way.
    try {
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (Ctx) {
        const ctx = new Ctx();
        if (ctx.state === 'suspended') void ctx.resume();
      }
    } catch {
      /* audio unlock is best-effort */
    }
    onDeploy();
  };

  return (
    <div className="splash">
      <MapBackdrop mapId={map} focus="center" />
      <div className="wrap splash-inner">
        <div>
          <button type="button" className="btn btn-sm btn-ghost" onClick={onCancel}>
            Back to servers
          </button>
        </div>

        <div className="splash-main">
          <div className="kicker rise">{room ? `Joining room ${room}` : 'Opening a new room'}</div>
          <h1 className="display splash-title rise" style={{ ['--i' as string]: 1 }}>
            {entry.title}
          </h1>
          <div className="splash-tags rise" style={{ ['--i' as string]: 2 }}>
            <span className="tag">{map}</span>
            {entry.sites && <span className="tag">A · B sites</span>}
            <span className={`tag${handle ? ' tag-accent' : ''}`}>
              {handle ? 'Signed in' : 'Guest · unrated'}
            </span>
          </div>
          {entry.blurb && (
            <p className="hero-copy rise" style={{ ['--i' as string]: 3, marginTop: 16 }}>
              {entry.blurb}
            </p>
          )}

          <div className="progress rise" style={{ ['--i' as string]: 4 }}>
            <div className="progress-row">
              <span>
                {error ? 'Map download failed' : ready ? 'Map loaded' : 'Downloading map'}
              </span>
              <b>{ready ? '100%' : `${formatBytes(progress.loaded, progress.total)} · ${pct}%`}</b>
            </div>
            <div className="progress-bar">
              <i style={{ ['--fill' as string]: (pct / 100).toFixed(2) }} />
            </div>
            {error && (
              <div className="error" style={{ marginTop: 10 }}>
                {error}. The game will try again when you deploy.
              </div>
            )}
          </div>

          <form
            className="splash-deploy rise"
            style={{ ['--i' as string]: 5 }}
            onSubmit={(e) => {
              e.preventDefault();
              deploy();
            }}
          >
            {handle ? (
              <div className="field">
                <span>Playing as</span>
                <div
                  className="display"
                  style={{ fontSize: 28, height: 36, display: 'flex', alignItems: 'center' }}
                >
                  {handle}
                </div>
              </div>
            ) : (
              <label className="field">
                <span>Callsign</span>
                <input
                  type="text"
                  className="input"
                  maxLength={16}
                  value={callsign}
                  onChange={(e) => onCallsignChange(e.target.value)}
                />
              </label>
            )}
            <button type="submit" className="btn btn-primary btn-lg" disabled={!ready && !error}>
              {ready || error ? 'Deploy' : `Loading ${pct}%`}
            </button>
          </form>
        </div>

        <div className="keys rise" style={{ ['--i' as string]: 6 }} aria-label="Controls">
          {KEYS.map((k) => (
            <div className="key" key={k.label}>
              <span style={{ display: 'flex', gap: 3 }}>
                {k.keys.map((c) => (
                  <span className="cap" key={c}>
                    {c}
                  </span>
                ))}
              </span>
              {k.label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
