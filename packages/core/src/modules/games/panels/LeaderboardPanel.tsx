import { useCallback, useEffect, useState } from 'react';

import { fetchLeaderboard, type LeaderRow } from '../games-api';

const GAMES = ['tictactoe'];

/** The ELO ladder for a game: harnesses ranked by match outcomes. */
export function LeaderboardPanel() {
  const [gameId, setGameId] = useState('tictactoe');
  const [rows, setRows] = useState<LeaderRow[]>([]);
  const [status, setStatus] = useState('');

  const load = useCallback(() => {
    setStatus('loading…');
    fetchLeaderboard(gameId)
      .then((r) => {
        setRows(r.entries);
        setStatus('');
      })
      .catch((e) => setStatus(String(e)));
  }, [gameId]);

  useEffect(() => load(), [load]);

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
        <strong>Ladder</strong>
        <select value={gameId} onChange={(e) => setGameId(e.target.value)}>
          {GAMES.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
        <button type="button" onClick={load}>
          Refresh
        </button>
        <span style={{ color: 'var(--text-dim)' }}>{status}</span>
      </div>

      {rows.length === 0 ? (
        <div style={{ color: 'var(--text-dim)' }}>No games played yet.</div>
      ) : (
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-dim)' }}>
              <th style={{ padding: '0.2rem 0.4rem' }}>#</th>
              <th style={{ padding: '0.2rem 0.4rem' }}>Player</th>
              <th style={{ padding: '0.2rem 0.4rem' }}>Rating</th>
              <th style={{ padding: '0.2rem 0.4rem' }}>W/L/D</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.account_id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '0.2rem 0.4rem', color: 'var(--text-dim)' }}>{i + 1}</td>
                <td style={{ padding: '0.2rem 0.4rem' }}>{r.display_name}</td>
                <td style={{ padding: '0.2rem 0.4rem', fontWeight: 700 }}>{r.rating}</td>
                <td style={{ padding: '0.2rem 0.4rem', color: 'var(--text-dim)' }}>
                  {r.wins}/{r.losses}/{r.draws}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
