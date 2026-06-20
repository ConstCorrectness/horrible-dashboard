import { useEffect, useState } from 'react';

import { useAgentContext } from '../../agent-context';
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
  const [searchQuery, setSearchQuery] = useState('');
  const [toast, setToast] = useState<string | null>(null);

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

  const triggerToast = (message: string) => {
    setToast(message);
    setTimeout(() => setToast(null), 3000);
  };

  // Filter channels based on search query
  const filteredChannels = channels.filter((c) => {
    const query = searchQuery.toLowerCase().trim();
    if (!query) return true;
    return (
      (c.topic && c.topic.toLowerCase().includes(query)) ||
      (c.club?.name && c.club.name.toLowerCase().includes(query)) ||
      c.users.some((u) => u.name && u.name.toLowerCase().includes(query))
    );
  });

  // Let the agent read the live rooms currently listed.
  useAgentContext(() => ({
    state,
    rooms: channels.map((c) => ({
      topic: c.topic || null,
      club: c.club?.name ?? null,
      speakers: c.num_speakers ?? 0,
      total: c.num_all ?? 0,
      people: c.users.map((u) => u.name).filter(Boolean),
    })),
  }));

  return (
    <div className="ch-rooms">
      <header className="ch-rooms-head">
        <h2>Live Rooms</h2>
        <div className="ch-rooms-controls">
          {state === 'ready' && (
            <div className="ch-search-container">
              <span className="ch-search-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" x2="16.65" y1="21" y2="16.65" />
                </svg>
              </span>
              <input
                className="ch-search-input"
                type="text"
                placeholder="Search topics, clubs, or users..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
          )}
          <button
            className={`ch-btn-refresh ${state === 'loading' ? 'spinning' : ''}`}
            onClick={() => void load()}
            disabled={state === 'loading'}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M23 4v6h-6" />
              <path d="M1 20v-6h6" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
            {state === 'loading' ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </header>

      {state === 'disconnected' && (
        <p className="dashboard-hint">
          Connect your Clubhouse account first (using the Clubhouse widget on the dashboard).
        </p>
      )}
      {state === 'error' && <p className="widget-error">{error}</p>}
      {state === 'ready' && channels.length === 0 && (
        <p className="dashboard-hint">No rooms are live right now.</p>
      )}
      {state === 'ready' && channels.length > 0 && filteredChannels.length === 0 && (
        <p className="dashboard-hint">No rooms match your search query.</p>
      )}

      <ul className="ch-room-list">
        {filteredChannels.map((c) => {
          const mainSpeakers = c.users.filter((u) => u.is_speaker).slice(0, 3);
          const otherSpeakersCount = Math.max(0, c.users.filter((u) => u.is_speaker).length - 3);
          
          return (
            <li key={c.channel} className="ch-room">
              <div className="ch-room-header">
                <h3 className="ch-room-topic">{c.topic || '(Untitled Room)'}</h3>
                {c.club?.name && (
                  <span className="ch-room-club-badge" title={c.club.name}>
                    {c.club.name}
                  </span>
                )}
              </div>

              <div className="ch-room-body">
                <div className="ch-avatar-stack">
                  {mainSpeakers.map((u) => {
                    const initials = u.name ? u.name.split(' ').map((n) => n[0]).join('').slice(0, 2) : '?';
                    return u.photo_url ? (
                      <img
                        key={u.user_id}
                        className="ch-avatar-stack-item"
                        src={u.photo_url}
                        alt={u.name || ''}
                        title={`${u.name}${u.is_moderator ? ' (Moderator)' : ''}`}
                      />
                    ) : (
                      <div
                        key={u.user_id}
                        className="ch-avatar-placeholder"
                        title={`${u.name}${u.is_moderator ? ' (Moderator)' : ''}`}
                      >
                        {initials}
                      </div>
                    );
                  })}
                  {otherSpeakersCount > 0 && (
                    <div className="ch-avatar-placeholder" title={`${otherSpeakersCount} more speakers`}>
                      +{otherSpeakersCount}
                    </div>
                  )}
                </div>

                <div className="ch-room-speakers-text">
                  {c.users.map((u, idx) => (
                    <span key={u.user_id || idx} style={{ fontWeight: u.is_moderator ? 600 : 'normal' }}>
                      {u.is_moderator && <span className="ch-pulse-dot" title="Moderator" />}
                      {u.name}
                      {idx < c.users.length - 1 ? ', ' : ''}
                    </span>
                  ))}
                </div>
              </div>

              <div className="ch-room-footer">
                <div className="ch-room-stats">
                  <div className="ch-stat-badge" title="Speakers">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                      <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
                      <line x1="12" x2="12" y1="19" y2="22" />
                    </svg>
                    <span>{c.num_speakers ?? 0}</span>
                  </div>
                  <div className="ch-stat-badge" title="Listeners">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
                      <circle cx="9" cy="7" r="4" />
                      <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
                      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                    </svg>
                    <span>{c.num_all ?? 0}</span>
                  </div>
                </div>
                <button
                  className="ch-btn-join"
                  onClick={() => triggerToast('Agora voice integration is coming in a future phase!')}
                >
                  Join Room
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {toast && <div className="ch-toast">{toast}</div>}
    </div>
  );
}
