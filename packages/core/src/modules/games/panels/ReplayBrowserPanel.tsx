import { useCallback, useEffect, useState } from 'react';

import { toastsStore } from '../../../toasts';
import { fetchReplays, publishReplay, type ReplaySummary } from '../games-api';
import { openReplay } from '../replay-focus';

type Scope = 'mine' | 'public';

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

/**
 * The replay browser: your own match history plus everything the community has
 * published — the trajectory explorer for studying how other harnesses win.
 */
export function ReplayBrowserPanel() {
  const [scope, setScope] = useState<Scope>('mine');
  const [rows, setRows] = useState<ReplaySummary[]>([]);
  const [status, setStatus] = useState('');

  const load = useCallback(() => {
    setStatus('loading…');
    fetchReplays(scope)
      .then((r) => {
        setRows(r.replays ?? []);
        setStatus(r.error ?? '');
      })
      .catch((e) => setStatus(String(e)));
  }, [scope]);

  useEffect(() => load(), [load]);

  const publish = (id: string) =>
    publishReplay(id).then((r) => {
      if (r.ok) {
        toastsStore.add('info', 'Games', 'Replay published');
        load();
      } else toastsStore.add('error', 'Games', r.error ?? 'publish failed');
    });

  return (
    <div
      style={{
        padding: '0.6rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        height: '100%',
        overflow: 'auto',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <strong>📼 Replays</strong>
        <button
          type="button"
          className={scope === 'mine' ? 'games-tab-active' : undefined}
          onClick={() => setScope('mine')}
        >
          My matches
        </button>
        <button
          type="button"
          className={scope === 'public' ? 'games-tab-active' : undefined}
          onClick={() => setScope('public')}
        >
          Public
        </button>
        <button type="button" onClick={load}>
          Refresh
        </button>
        <span style={{ color: 'var(--text-dim)' }}>{status}</span>
      </div>

      {rows.length === 0 ? (
        <div style={{ color: 'var(--text-dim)' }}>
          {scope === 'mine'
            ? 'No matches recorded yet — play one and it lands here.'
            : 'Nothing published yet. Finish a match and hit Publish in the viewer.'}
        </div>
      ) : (
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-dim)' }}>
              <th style={{ padding: '0.2rem 0.4rem' }}>Game</th>
              <th style={{ padding: '0.2rem 0.4rem' }}>Players</th>
              <th style={{ padding: '0.2rem 0.4rem' }}>Result</th>
              <th style={{ padding: '0.2rem 0.4rem' }}>When</th>
              <th style={{ padding: '0.2rem 0.4rem' }} />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '0.2rem 0.4rem' }}>{r.game_id}</td>
                <td style={{ padding: '0.2rem 0.4rem' }}>{r.seats.join(' vs ')}</td>
                <td style={{ padding: '0.2rem 0.4rem' }}>
                  {r.winner !== null ? `🏆 ${r.seats[r.winner]}` : '🤝 draw'}
                </td>
                <td style={{ padding: '0.2rem 0.4rem', color: 'var(--text-dim)' }}>
                  {when(r.created_at)}
                </td>
                <td style={{ padding: '0.2rem 0.4rem', whiteSpace: 'nowrap' }}>
                  <button type="button" onClick={() => openReplay(r.id)}>
                    ▶ Watch
                  </button>{' '}
                  {scope === 'mine' && !r.public && (
                    <button type="button" onClick={() => publish(r.id)}>
                      Publish
                    </button>
                  )}
                  {r.public && <span title="public">🌐</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
