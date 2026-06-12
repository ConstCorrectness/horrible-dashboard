import { useEffect, useState } from 'react';

import { getClubhouseChannels, getClubhouseStatus, type Channel } from './api';

/**
 * Phase 1 browse panel: lists the live Clubhouse rooms for the connected
 * account. Read-only — joining a room (PubNub presence + Agora audio) is a later
 * phase. See docs/modules/clubhouse.md.
 */
export function RoomsPanel() {
  const [state, setState] = useState<'loading' | 'disconnected' | 'ready' | 'error'>('loading');
  const [channels, setChannels] = useState<Channel[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setState('loading');
    setError(null);
    try {
      const status = await getClubhouseStatus();
      if (!status.connected) {
        setState('disconnected');
        return;
      }
      const res = await getClubhouseChannels();
      setChannels(res.channels ?? []);
      setState('ready');
    } catch (e) {
      setError(String(e));
      setState('error');
    }
  };

  useEffect(() => {
    void load();
  }, []);

  return (
    <div className="ch-rooms">
      <header className="ch-rooms-head">
        <h2>Live rooms</h2>
        <button onClick={() => void load()} disabled={state === 'loading'}>
          {state === 'loading' ? 'Loading…' : 'Refresh'}
        </button>
      </header>

      {state === 'disconnected' && (
        <p className="dashboard-hint">
          Connect your Clubhouse account first (the Clubhouse widget on the dashboard).
        </p>
      )}
      {state === 'error' && <p className="widget-error">{error}</p>}
      {state === 'ready' && channels.length === 0 && (
        <p className="dashboard-hint">No rooms are live right now.</p>
      )}

      <ul className="ch-room-list">
        {channels.map((c) => (
          <li key={c.channel} className="ch-room">
            <div className="ch-room-topic">{c.topic || '(untitled room)'}</div>
            <div className="ch-room-meta">
              {c.club?.name && <span className="ch-room-club">{c.club.name}</span>}
              <span>🎙 {c.num_speakers ?? 0}</span>
              <span>👥 {c.num_all ?? 0}</span>
            </div>
            <div className="ch-room-people">
              {c.users
                .slice(0, 4)
                .map((u) => u.name)
                .filter(Boolean)
                .join(', ')}
              {c.users.length > 4 && ` +${c.users.length - 4}`}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
