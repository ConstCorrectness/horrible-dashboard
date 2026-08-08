import { useEffect, useState } from 'react';

import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';

import { claimChallengeDraft, onChallengeDraft, type ChallengeTarget } from '../challenge-draft';
import { requestChallenges } from '../challenge-focus';
import { gamesListTables, useGames, gamesQueueLeave, type TableInfo } from '../game-ws';
import { type GameCatalogEntry } from '../games-api';
import { joinTableLive, watchTableLive } from '../matchmaking';
import { openHarnessFor } from '../selected-game';
import { openGamesSection } from '../hub-section';
import { ChallengeDraftCard, IncomingOfferCard } from './ChallengeCards';
import { gameAccent, gameIcon } from '../game-identity';
import { FirstRunHero } from './FirstRunHero';
import { GamesHero } from './GamesHero';
import { MatchSetupCard } from './MatchSetup';
import { GamesMui } from '../mui-theme';
import { useSetting } from '../../../settings';

/**
 * The hub's default section: **start a match**.
 *
 * Everything about *how* a match is configured now lives in one place —
 * `MatchSetupCard`, the two-seat VS card. This file is the surround: the empty
 * state, the incoming-challenge cards, the dev-kit shortcuts and the server
 * browser.
 *
 * It used to hold the configuration itself, in duplicate: a MOVE POLICY toggle
 * group rendered twice from the same state, a queue difficulty rendered twice
 * (once as toggles, once as a dropdown), two Deselect buttons, and four start
 * buttons whose meaning depended on a control under a different heading. All of
 * that collapsed into the seat card. See docs/modules/games.mdx.
 */

const GAME_DESCRIPTIONS: Record<string, string> = {
  tictactoe:
    'Classic 3-in-a-row board game. Excellent for testing basic search, minimax algorithms, and heuristic evaluation functions.',
  connect_four:
    'Drop checkers to line up four of your color. Requires deeper search trees, alpha-beta pruning, and column valuation strategy.',
  holdem:
    "Limit Texas Hold'em poker. Evaluates reasoning under imperfect information, probability calculation, and opponent bluff detection.",
  rag_race:
    'Retrieval-Augmented Generation challenge. Build a pipeline to query documents and answer questions accurately under strict latency limits.',
  code_golf:
    'Write the shortest possible Python code to satisfy unit tests. Tests code generation efficiency and syntax minimization.',
  test_duel:
    "Generate unit tests to cover branches of an opponent's code while defending your own. Tests adversarial test generation.",
  bug_hunt:
    'Locate and patch bugs in a multi-file Python codebase. Evaluates agent diagnostic logs, code reasoning, and surgical code edits.',
  arena:
    'Real-time survival arena. Navigate, gather resources, and outlast other agents in a dynamic grid world.',
  fighter:
    '2D arcade street fighting game. Tests quick real-time state machine policies, hitbox management, and frame-data timing.',
  vizdoom_toy:
    '3D Doom visual combat simulator. Tests reinforcement learning, visual field parsing, and continuous movement control.',
  vizdoom_duel:
    'Real networked 1v1 Doom deathmatch — two agents on one shared map, scored by frags. Tests real-time combat policy, positioning, and target acquisition.',
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

/** The server browser, shared by the empty state and the per-game view. */
function ServerBrowser({
  title,
  tables,
  nameOf,
  connected,
  emptyHint,
}: {
  title: string;
  tables: TableInfo[];
  nameOf: (id: string) => string;
  connected: boolean;
  emptyHint: string;
}) {
  return (
    <>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '0.4rem',
          marginTop: '1rem',
        }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 800, color: 'text.secondary' }}>
          {title}
        </Typography>
        <Button
          size="small"
          color="inherit"
          onClick={() => gamesListTables()}
          sx={{ py: 0.1, minWidth: 0, textTransform: 'none' }}
        >
          ↻ Refresh List
        </Button>
      </div>

      <div className="games-server-browser">
        <div className="games-server-header">
          <span>Server</span>
          <span>Status</span>
          <span>Players</span>
          <span>Ping</span>
          <span>Action</span>
        </div>

        {tables.length === 0 ? (
          <div
            style={{
              padding: '2rem 1rem',
              textAlign: 'center',
              color: 'var(--text-dim)',
              fontSize: '0.8rem',
            }}
          >
            {connected
              ? emptyHint
              : 'Servers appear once connected — that happens automatically when you play.'}
          </div>
        ) : (
          tables.map((t) => (
            <ServerRow
              key={t.id}
              table={t}
              gameName={nameOf(t.game_id)}
              gameIcon={gameIcon(t.game_id)}
            />
          ))
        )}
      </div>
    </>
  );
}

export function PlaySection({
  games,
  selectedGame,
  setSelectedGame,
}: {
  games: GameCatalogEntry[];
  selectedGame: string | null;
  setSelectedGame: (id: string | null) => void;
}) {
  const { connected, tables, queue } = useGames();
  const [draft, setDraft] = useState<ChallengeTarget | null>(() => claimChallengeDraft());
  const onboarded = useSetting<boolean>('games.onboarded') === true;

  useEffect(() => onChallengeDraft(setDraft), []);

  useEffect(() => {
    if (connected) gamesListTables();
  }, [connected]);

  const nameOf = (id: string) => games.find((g) => g.id === id)?.name ?? id;
  const filteredTables = selectedGame ? tables.filter((t) => t.game_id === selectedGame) : tables;
  const g = selectedGame ? games.find((x) => x.id === selectedGame) : undefined;

  // The setup card shows the queue for the game you are looking at. A queue for a
  // *different* game would otherwise vanish when you browse away from it, so it
  // gets a banner of its own here.
  const strayQueue = queue && queue.gameId !== selectedGame ? queue : null;

  return (
    <GamesMui>
      {!onboarded && <FirstRunHero />}

      <IncomingOfferCard games={games} />
      {draft && <ChallengeDraftCard target={draft} games={games} onDone={() => setDraft(null)} />}

      {strayQueue && (
        <div className="games-stray-queue">
          <span className="games-radar-scan">
            <span className="games-radar-line" />
          </span>
          <span>
            Queued for <strong>{nameOf(strayQueue.gameId)}</strong> · {strayQueue.waitingS}s
          </span>
          <Button size="small" color="inherit" onClick={() => setSelectedGame(strayQueue.gameId)}>
            Show
          </Button>
          <Button size="small" color="error" onClick={() => gamesQueueLeave()}>
            Leave
          </Button>
        </div>
      )}

      {!g ? (
        <>
          <GamesHero games={games} setSelectedGame={setSelectedGame} />
          <ServerBrowser
            title="🌐 Active Server Browser (All Games)"
            tables={filteredTables}
            nameOf={nameOf}
            connected={connected}
            emptyHint="No servers running yet. Select a game from the library on the left to set up a match or host a table."
          />
        </>
      ) : (
        <>
          <div className="games-play-topbar">
            <Typography variant="body2" sx={{ color: 'text.secondary', fontSize: '0.78rem' }}>
              {GAME_DESCRIPTIONS[g.id] ??
                `Play ${g.name} against other agents or practice with your own.`}
            </Typography>
            <Button
              variant="text"
              color="inherit"
              size="small"
              sx={{ textTransform: 'none', fontSize: '0.75rem', flex: '0 0 auto' }}
              onClick={() => setSelectedGame(null)}
            >
              🌐 Deselect Game
            </Button>
          </div>

          <MatchSetupCard game={g} />

          <div className="games-devkit">
            <button
              type="button"
              className="games-devkit-card"
              style={{ '--tile-accent': gameAccent(g.id) } as React.CSSProperties}
              onClick={() => openHarnessFor(g.id)}
            >
              <span className="games-devkit-icon" aria-hidden>
                🛠
              </span>
              <span className="games-devkit-title">Build</span>
              <span className="games-devkit-hint">
                Author agent policy code, equip tools, ground docs.
              </span>
            </button>
            <button
              type="button"
              className="games-devkit-card"
              style={{ '--tile-accent': gameAccent(g.id) } as React.CSSProperties}
              onClick={() => openGamesSection('train')}
            >
              <span className="games-devkit-icon" aria-hidden>
                🎯
              </span>
              <span className="games-devkit-title">Train</span>
              <span className="games-devkit-hint">
                Run one turn against a sample position and read the trace. No stakes.
              </span>
            </button>
            <button
              type="button"
              className="games-devkit-card"
              style={{ '--tile-accent': gameAccent(g.id) } as React.CSSProperties}
              onClick={() => requestChallenges(g.id)}
            >
              <span className="games-devkit-icon" aria-hidden>
                📋
              </span>
              <span className="games-devkit-title">Challenges</span>
              <span className="games-devkit-hint">
                Scenario-grade the harness against known-correct positions.
              </span>
            </button>
          </div>

          <ServerBrowser
            title={`🌐 Active Server Browser (${g.name} servers)`}
            tables={filteredTables}
            nameOf={nameOf}
            connected={connected}
            emptyHint={`No ${g.name} servers running. Pick "Open Table" on the setup card to host one.`}
          />
        </>
      )}
    </GamesMui>
  );
}
