import { useEffect, useState } from 'react';

import { revealRegionView } from '../../../layout/controller';
import { useSetting } from '../../../settings';
import { claimChallengeDraft, onChallengeDraft, type ChallengeTarget } from '../challenge-draft';
import { requestChallenges } from '../challenge-focus';
import { gamesListTables, useGames } from '../game-ws';
import { fetchGamesCatalog, type GameCatalogEntry } from '../games-api';
import { hostOpenTable, joinTableLive, playVsOwnAgent, watchTableLive } from '../matchmaking';
import { ChallengeDraftCard, IncomingOfferCard, RankedCard } from './ChallengeCards';
import { FirstRunHero } from './FirstRunHero';

// Icon per catalog game on the cards; anything unrecognized gets the die.
const GAME_ICONS: Record<string, string> = {
  tictactoe: '❌',
  connect_four: '🔴',
  holdem: '🃏',
  rag_race: '📚',
  code_golf: '⛳',
  test_duel: '⚖️',
  bug_hunt: '🐛',
  arena: '🤖',
  fighter: '🥊',
};

/**
 * The hub's default tab: start a match. Nothing here is gated on the connection
 * — ▶ Play / Find match / Join connect the node themselves (matchmaking.ts).
 */
export function PlaySection() {
  const { connected, tables } = useGames();
  const [games, setGames] = useState<GameCatalogEntry[]>([]);
  const [draft, setDraft] = useState<ChallengeTarget | null>(() => claimChallengeDraft());
  const [hostGame, setHostGame] = useState('tictactoe');
  const onboarded = useSetting<boolean>('games.onboarded') === true;

  useEffect(() => {
    fetchGamesCatalog().then(setGames);
  }, []);

  useEffect(() => onChallengeDraft(setDraft), []);

  useEffect(() => {
    if (connected) gamesListTables();
  }, [connected]);

  return (
    <>
      {!onboarded && <FirstRunHero />}

      <IncomingOfferCard games={games} />
      {draft && <ChallengeDraftCard target={draft} games={games} onDone={() => setDraft(null)} />}

      <RankedCard games={games} />

      <div>
        <div style={{ color: 'var(--text-dim)', marginBottom: '0.3rem' }}>
          Play against your own agent
        </div>
        <div className="games-cards">
          {games.map((g) => (
            <div key={g.id} className="games-card">
              <span className="games-card-icon">{GAME_ICONS[g.id] ?? '🎲'}</span>
              <span className="games-card-name">{g.name}</span>
              <div className="games-card-actions">
                <button
                  type="button"
                  className="games-play-btn"
                  onClick={() => void playVsOwnAgent(g.id)}
                >
                  ▶ Play
                </button>
                <button
                  type="button"
                  className="games-chip-btn"
                  title={`${g.name} challenges — grade your harness off-table`}
                  onClick={() => requestChallenges(g.id)}
                >
                  🎯
                </button>
              </div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: '0.4rem' }}>
          <button
            type="button"
            onClick={() => revealRegionView('games.loadout')}
            style={{ fontSize: '0.72rem' }}
          >
            Edit agent harness →
          </button>
        </div>
      </div>

      <div>
        <div
          style={{
            color: 'var(--text-dim)',
            marginBottom: '0.3rem',
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
          }}
        >
          Open tables
          <button type="button" onClick={() => gamesListTables()} style={{ fontSize: '0.7rem' }}>
            refresh
          </button>
          <span
            style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.3rem' }}
          >
            <select value={hostGame} onChange={(e) => setHostGame(e.target.value)}>
              {games.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              style={{ fontSize: '0.72rem' }}
              title="Host an open table another player can Join (e.g. your other machine) — no sparring bot"
              onClick={() => void hostOpenTable(hostGame)}
            >
              Host a table
            </button>
          </span>
        </div>
        {tables.length === 0 ? (
          <div style={{ color: 'var(--text-dim)' }}>
            {connected
              ? 'No tables yet.'
              : 'Tables appear once connected — that happens automatically when you play.'}
          </div>
        ) : (
          <ul
            style={{
              listStyle: 'none',
              padding: 0,
              margin: 0,
              display: 'flex',
              flexDirection: 'column',
              gap: '0.25rem',
            }}
          >
            {tables.map((t) => (
              <li key={t.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <code>{t.game_id}</code>
                <span style={{ color: 'var(--text-dim)' }}>
                  {t.seats.filter(Boolean).length}/{t.capacity} · {t.status}
                </span>
                {t.status === 'open' && (
                  <button type="button" onClick={() => void joinTableLive(t.id)}>
                    Join
                  </button>
                )}
                {t.status === 'playing' && (
                  <button type="button" onClick={() => void watchTableLive(t.id)}>
                    👁 Watch
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
