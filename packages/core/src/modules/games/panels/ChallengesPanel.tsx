import { useCallback, useEffect, useState } from 'react';

import { claimChallengeFocus, onChallengeFocus } from '../challenge-focus';
import { toastsStore } from '../../../toasts';
import { ensureConnected, gamesRunChallenges, useGames } from '../game-ws';
import {
  fetchChallengeLeaderboard,
  fetchGamesCatalog,
  type ChallengeRow,
  type GameCatalogEntry,
} from '../games-api';

/**
 * The challenge track: run your harness against category scenarios (off-table) and
 * see a graded report card — how many you got right, and which categories you cover.
 * The server owns the answers, so this measures the harness, not luck. Pick which
 * game's challenge set to run from the catalog.
 */
export function ChallengesPanel() {
  const { challengeRunning, challengeReport } = useGames();
  const [games, setGames] = useState<GameCatalogEntry[]>([]);
  const [gameId, setGameId] = useState('tictactoe');
  const [board, setBoard] = useState<ChallengeRow[]>([]);

  useEffect(() => {
    fetchGamesCatalog().then(setGames);
  }, []);

  // A lobby card's 🎯 shortcut pre-selects that game's scenario set — claim any
  // buffered request on mount, then follow live ones while open.
  useEffect(() => {
    const focused = claimChallengeFocus();
    if (focused) setGameId(focused);
    return onChallengeFocus(setGameId);
  }, []);

  const loadBoard = useCallback(() => {
    fetchChallengeLeaderboard(gameId)
      .then((r) => setBoard(r.entries))
      .catch(() => setBoard([]));
  }, [gameId]);

  useEffect(() => loadBoard(), [loadBoard]);
  // Refresh the board when a run finishes (a new best may have landed).
  useEffect(() => {
    if (challengeReport) loadBoard();
  }, [challengeReport, loadBoard]);

  return (
    <div
      style={{
        padding: '0.6rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
        height: '100%',
        overflow: 'auto',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <strong>Challenge track</strong>
        <select value={gameId} onChange={(e) => setGameId(e.target.value)}>
          {games.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={challengeRunning}
          onClick={() =>
            void ensureConnected(false)
              .then(() => gamesRunChallenges(gameId))
              .catch((e: Error) => toastsStore.add('error', 'Games', e.message))
          }
        >
          {challengeRunning ? 'Running…' : 'Run my harness'}
        </button>
      </div>

      {challengeReport && (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 6,
            padding: '0.5rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.35rem',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
            <span style={{ fontSize: '1.3rem', fontWeight: 700 }}>
              {challengeReport.correct}/{challengeReport.total}
            </span>
            <span style={{ color: 'var(--text-dim)' }}>
              {Math.round(challengeReport.score * 100)}% · {challengeReport.covered}/
              {challengeReport.category_count} categories covered
            </span>
            {challengeReport.best && (
              <span style={{ color: 'var(--success, #3fb950)', fontWeight: 700 }}>new best!</span>
            )}
          </div>
          <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
            {Object.entries(challengeReport.categories).map(([cat, c]) => {
              const full = c.passed === c.total;
              return (
                <span
                  key={cat}
                  style={{
                    padding: '0.1rem 0.4rem',
                    borderRadius: 4,
                    border: '1px solid var(--border)',
                    color: full ? 'var(--success, #3fb950)' : 'var(--text-dim)',
                  }}
                >
                  {cat} {c.passed}/{c.total}
                </span>
              );
            })}
          </div>
        </div>
      )}

      <div>
        <div style={{ color: 'var(--text-dim)', margin: '0.2rem 0' }}>
          Challenge leaderboard{' '}
          <button type="button" onClick={loadBoard} style={{ fontSize: '0.7rem' }}>
            refresh
          </button>
        </div>
        {board.length === 0 ? (
          <div style={{ color: 'var(--text-dim)' }}>No attempts yet.</div>
        ) : (
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--text-dim)' }}>
                <th style={{ padding: '0.2rem 0.4rem' }}>#</th>
                <th style={{ padding: '0.2rem 0.4rem' }}>Player</th>
                <th style={{ padding: '0.2rem 0.4rem' }}>Score</th>
              </tr>
            </thead>
            <tbody>
              {board.map((r, i) => (
                <tr key={r.account_id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.2rem 0.4rem', color: 'var(--text-dim)' }}>{i + 1}</td>
                  <td style={{ padding: '0.2rem 0.4rem' }}>{r.display_name}</td>
                  <td style={{ padding: '0.2rem 0.4rem', fontWeight: 700 }}>
                    {r.correct}/{r.total}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
