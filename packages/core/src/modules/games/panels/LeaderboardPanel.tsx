import { useCallback, useEffect, useState } from 'react';

import {
  fetchGamesCatalog,
  fetchLeaderboard,
  type GameCatalogEntry,
  type LeaderRow,
} from '../games-api';

const TIER_ICONS: Record<string, string> = {
  placement: '⏳',
  bronze: '🥉',
  silver: '🥈',
  gold: '🥇',
  platinum: '💠',
  diamond: '💎',
  master: '👑',
  grandmaster: '🔱',
};

/** The ranked ladder for a game: players by rating, with tier chips. Ratings stay
 * masked (⏳ placement) until a player's placement matches are in. */
export function LeaderboardPanel() {
  const [games, setGames] = useState<GameCatalogEntry[]>([]);
  const [gameId, setGameId] = useState('tictactoe');
  const [rows, setRows] = useState<LeaderRow[]>([]);
  const [status, setStatus] = useState('');

  useEffect(() => {
    fetchGamesCatalog().then(setGames);
  }, []);

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
          {(games.length ? games : [{ id: 'tictactoe', name: 'Tic-Tac-Toe' }]).map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
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
              <th style={{ padding: '0.2rem 0.4rem' }}>Tier</th>
              <th style={{ padding: '0.2rem 0.4rem' }}>Rating</th>
              <th style={{ padding: '0.2rem 0.4rem' }}>W/L/D</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.account_id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '0.2rem 0.4rem', color: 'var(--text-dim)' }}>{i + 1}</td>
                <td style={{ padding: '0.2rem 0.4rem' }}>{r.display_name}</td>
                <td style={{ padding: '0.2rem 0.4rem' }}>
                  {r.tier ? (
                    <span className="games-tier-chip" data-tier={r.tier}>
                      {TIER_ICONS[r.tier] ?? ''} {r.tier}
                      {r.tier === 'placement' ? ` ${r.placement_games ?? 0}/5` : ''}
                    </span>
                  ) : (
                    '—'
                  )}
                </td>
                <td style={{ padding: '0.2rem 0.4rem', fontWeight: 700 }}>{r.rating ?? '···'}</td>
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
