import { useEffect, useRef, useState } from 'react';

import { onTrainingEvent } from '../client';

const dim = { color: 'var(--text-dim)' } as const;

/**
 * Live rollout frames: renders the latest `frame` event (gym env renders and
 * anything else `horrible_train.frame()` emits), rAF-throttled like the
 * visualizer widget. Singleton.
 */
export function RolloutPane() {
  const [src, setSrc] = useState<string | null>(null);
  const [meta, setMeta] = useState('');
  const [fps, setFps] = useState(0);
  const pending = useRef<string | null>(null);
  const frameHandle = useRef<number | null>(null);
  const counter = useRef({ n: 0, since: performance.now() });

  useEffect(() => {
    const unsub = onTrainingEvent('frame', (d) => {
      pending.current = d.dataUrl;
      const c = counter.current;
      c.n += 1;
      const now = performance.now();
      if (now - c.since >= 1000) {
        setFps(Math.round((c.n * 1000) / (now - c.since)));
        c.n = 0;
        c.since = now;
      }
      setMeta(`${d.projectId}${d.source ? ` · ${d.source}` : ''}`);
      if (frameHandle.current == null) {
        frameHandle.current = requestAnimationFrame(() => {
          frameHandle.current = null;
          if (pending.current) setSrc(pending.current);
        });
      }
    });
    return () => {
      unsub();
      if (frameHandle.current != null) cancelAnimationFrame(frameHandle.current);
    };
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          display: 'flex',
          gap: '0.5rem',
          padding: '0.25rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.75rem',
          ...dim,
        }}
      >
        <span>{meta || 'rollout stream'}</span>
        <span style={{ flex: 1 }} />
        {fps > 0 && <span>{fps} fps</span>}
      </div>
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'hidden',
          background: '#000',
        }}
      >
        {src ? (
          <img
            src={src}
            alt="environment rollout"
            style={{ maxWidth: '100%', maxHeight: '100%', imageRendering: 'pixelated' }}
          />
        ) : (
          <span style={{ fontSize: '0.8rem', ...dim }}>
            No frames yet — call <code>horrible_train.frame(env.render())</code> in a rollout loop.
          </span>
        )}
      </div>
    </div>
  );
}
