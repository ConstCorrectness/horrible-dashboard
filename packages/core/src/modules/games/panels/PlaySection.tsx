import { useEffect, useState } from 'react';

import { revealRegionView } from '../../../layout/controller';
import { useSetting } from '../../../settings';
import { claimChallengeDraft, onChallengeDraft, type ChallengeTarget } from '../challenge-draft';
import { requestChallenges } from '../challenge-focus';
import { gamesListTables, useGames, type TableInfo } from '../game-ws';
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
  vizdoom_toy: '🔫',
};

/** Simulated ping bars (4 bars, filled based on "strength"). */
function PingBars({ strength = 4 }: { strength?: number }) {
  return (
    <div className="games-server-ping">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className={`games-ping-bar${i <= strength ? ' active' : ''}`} />
      ))}
    </div>
  );
}

/** Occupancy dots: filled for seated players, hollow for empty. */
function OccupancyDots({ seated, capacity }: { seated: number; capacity: number }) {
  return (
    <div className="games-server-occupancy">
      {Array.from({ length: capacity }, (_, i) => (
        <div key={i} className={`games-occupancy-dot${i < seated ? ' filled' : ''}`} />
      ))}
      <span style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginLeft: '0.15rem' }}>
        {seated}/{capacity}
      </span>
    </div>
  );
}

/** Status pill */
function StatusBadge({ status }: { status: string }) {
  return <span className={`games-status-badge ${status}`}>{status}</span>;
}

/** A single row in the server browser table */
function ServerRow({
  table,
  gameName,
  gameIcon,
}: {
  table: TableInfo;
  gameName: string;
  gameIcon: string;
}) {
  const seated = table.seats.filter(Boolean).length;

  return (
    <div className="games-server-row">
      <div className="games-server-mode">
        <span className="games-server-mode-icon">{gameIcon}</span>
        <span>{gameName}</span>
        <span
          style={{
            fontSize: '0.65rem',
            color: 'var(--text-dim)',
            fontWeight: 400,
            fontFamily: 'monospace',
          }}
        >
          #{table.id.slice(0, 6)}
        </span>
      </div>
      <StatusBadge status={table.status} />
      <OccupancyDots seated={seated} capacity={table.capacity} />
      <PingBars strength={4} />
      <div>
        {table.status === 'open' && (
          <button
            type="button"
            className="games-join-btn"
            onClick={() => void joinTableLive(table.id)}
          >
            ▶ JOIN
          </button>
        )}
        {table.status === 'playing' && (
          <button
            type="button"
            className="games-watch-btn"
            onClick={() => void watchTableLive(table.id)}
          >
            👁 WATCH
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * The hub's default tab: start a match. Inspired by play-cs.com's server
 * browser — game modes as clickable buttons across the top, tabular server
 * list below with status badges, occupancy dots, ping bars, and actions.
 */
export function PlaySection() {
  const { connected, tables } = useGames();
  const [games, setGames] = useState<GameCatalogEntry[]>([]);
  const [draft, setDraft] = useState<ChallengeTarget | null>(() => claimChallengeDraft());
  const [selectedGame, setSelectedGame] = useState<string | null>(null);
  const onboarded = useSetting<boolean>('games.onboarded') === true;

  useEffect(() => {
    fetchGamesCatalog().then(setGames);
  }, []);

  useEffect(() => onChallengeDraft(setDraft), []);

  useEffect(() => {
    if (connected) gamesListTables();
  }, [connected]);

  // Build a name lookup from catalog
  const nameOf = (id: string) => games.find((g) => g.id === id)?.name ?? id;

  // Filter tables by selected game (null = show all)
  const filteredTables = selectedGame ? tables.filter((t) => t.game_id === selectedGame) : tables;

  return (
    <>
      {!onboarded && <FirstRunHero />}

      <IncomingOfferCard games={games} />
      {draft && <ChallengeDraftCard target={draft} games={games} onDone={() => setDraft(null)} />}

      <RankedCard games={games} />

      {/* ── Game Mode Selector (play-cs.com style horizontal buttons) ── */}
      <div className="games-host-selector">
        <button
          type="button"
          className={`games-host-btn${selectedGame === null ? ' active' : ''}`}
          onClick={() => setSelectedGame(null)}
        >
          <span className="games-host-icon">🌐</span>
          All Games
        </button>
        {games.map((g) => (
          <button
            key={g.id}
            type="button"
            className={`games-host-btn${selectedGame === g.id ? ' active' : ''}`}
            onClick={() => setSelectedGame(g.id)}
          >
            <span className="games-host-icon">{GAME_ICONS[g.id] ?? '🎲'}</span>
            {g.name}
          </button>
        ))}
      </div>

      {/* ── Quick Actions Bar ── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          flexWrap: 'wrap',
        }}
      >
        <button
          type="button"
          className="games-host-deploy-btn"
          onClick={() => void playVsOwnAgent(selectedGame ?? games[0]?.id ?? 'tictactoe')}
        >
          ▶ Play vs My Agent
        </button>
        <button
          type="button"
          className="games-host-deploy-btn"
          style={{
            background: 'linear-gradient(135deg, #8b5cf6 0%, #6d28d9 100%)',
            color: '#f5f3ff',
            boxShadow: '0 2px 6px rgba(139, 92, 246, 0.2)',
          }}
          onClick={() => void hostOpenTable(selectedGame ?? games[0]?.id ?? 'tictactoe')}
          title="Create a table that another player (or your other machine) can join"
        >
          + Create Server
        </button>
        <button
          type="button"
          className="games-watch-btn"
          style={{ marginLeft: '0.15rem' }}
          onClick={() => requestChallenges(selectedGame ?? games[0]?.id ?? 'tictactoe')}
          title="Challenges — grade your harness off-table"
        >
          🎯 Challenges
        </button>
        <button
          type="button"
          className="games-watch-btn"
          onClick={() => revealRegionView('games.loadout')}
        >
          ⚙ Edit Harness
        </button>
        <button
          type="button"
          className="games-watch-btn"
          onClick={() => gamesListTables()}
          title="Refresh the server list"
        >
          ↻ Refresh
        </button>
      </div>

      {/* ── Server Browser Table ── */}
      <div className="games-server-browser">
        <div className="games-server-header">
          <span>Server</span>
          <span>Status</span>
          <span>Players</span>
          <span>Ping</span>
          <span>Action</span>
        </div>

        {filteredTables.length === 0 ? (
          <div
            style={{
              padding: '2rem 1rem',
              textAlign: 'center',
              color: 'var(--text-dim)',
              fontSize: '0.8rem',
            }}
          >
            {connected
              ? selectedGame
                ? `No ${nameOf(selectedGame)} servers running. Hit "+ Create Server" to start one.`
                : 'No servers running yet. Start a match or create a server above.'
              : 'Servers appear once connected — that happens automatically when you play.'}
          </div>
        ) : (
          filteredTables.map((t) => (
            <ServerRow
              key={t.id}
              table={t}
              gameName={nameOf(t.game_id)}
              gameIcon={GAME_ICONS[t.game_id] ?? '🎲'}
            />
          ))
        )}
      </div>
    </>
  );
}
