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
