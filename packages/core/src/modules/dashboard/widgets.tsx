import { useEffect, useState } from 'react';

import { apiGet } from '../../api';

interface Health {
  status: string;
  app: string;
  version: string;
}

export function WelcomeWidget() {
  return (
    <div>
      <p>
        Welcome to <strong>horrible-dashboard</strong> — your one-stop app for everything.
      </p>
      <p>
        Press <kbd>Ctrl</kbd>+<kbd>K</kbd> for the command palette.
      </p>
    </div>
  );
}

export function BackendStatusWidget() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      apiGet<Health>('/health')
        .then((h) => {
          if (cancelled) return;
          setHealth(h);
          setError(null);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          setHealth(null);
          setError(String(e));
        });
    };
    poll();
    const timer = setInterval(poll, 10_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (error) {
    return <p className="widget-error">Backend unreachable — is it running on port 8000?</p>;
  }
  if (!health) return <p>Checking…</p>;
  return (
    <p>
      Backend <strong>{health.status}</strong> — {health.app} v{health.version}
    </p>
  );
}


export function GameWidget() {
  const [size, setSize] = useState({ width: 640, height: 480 });
  const [src, setSrc] = useState('https://assault.cubers.net/play'); // generic assault cube web port placeholder

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <label style={{ fontSize: '12px' }}>
          URL: <input type="text" value={src} onChange={e => setSrc(e.target.value)} style={{ padding: '2px' }} />
        </label>
        <label style={{ fontSize: '12px' }}>
          W: <input type="number" value={size.width} onChange={e => setSize({ ...size, width: Number(e.target.value) })} style={{ width: '60px', padding: '2px' }} />
        </label>
        <label style={{ fontSize: '12px' }}>
          H: <input type="number" value={size.height} onChange={e => setSize({ ...size, height: Number(e.target.value) })} style={{ width: '60px', padding: '2px' }} />
        </label>
      </div>
      <div style={{
        resize: 'both',
        overflow: 'hidden',
        width: size.width,
        height: size.height,
        minWidth: '300px',
        minHeight: '200px',
        border: '1px solid #ccc',
        background: '#000'
      }}>
        <iframe
          src={src}
          style={{ width: '100%', height: '100%', border: 'none' }}
          title="Game Harness"
          allowFullScreen
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        />
      </div>
    </div>
  );
}
