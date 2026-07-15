import { useEffect, useState } from 'react';

import Typography from '@mui/material/Typography';

import { revealRegionView } from '../../../layout/controller';
import { registry } from '../../../registry';
import {
  gamesQueueLeave,
  rematchOffer,
  useGames,
  type PublicState,
  type SeatProfile,
} from '../game-ws';
import { fetchGamesCatalog, type GameCatalogEntry } from '../games-api';
import { playVsOwnAgent } from '../matchmaking';
import { phaseLabel } from '../phase-label';
import { openReplay } from '../replay-focus';
import { ArenaBoard } from './ArenaBoard';
import { BugHuntBoard } from './BugHuntBoard';
import { CodeGolfBoard } from './CodeGolfBoard';
import { ConnectFourBoard } from './ConnectFourBoard';
import { FighterCanvas } from './FighterCanvas';
import { PokerBoard } from './PokerBoard';
import { RagRaceBoard } from './RagRaceBoard';
import { TestDuelBoard } from './TestDuelBoard';
import { TicTacToeBoard } from './TicTacToeBoard';
import { VizDoomBoard } from './VizDoomBoard';
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
  vizdoom_duel: '💀',
};

const GAME_ACCENT: Record<string, string> = {
  tictactoe: '#fb7185',
  connect_four: '#fbbf24',
  holdem: '#a78bfa',
  rag_race: '#60a5fa',
  code_golf: '#4ade80',
  test_duel: '#94a3b8',
  bug_hunt: '#84cc16',
  arena: '#fb923c',
  fighter: '#f87171',
  vizdoom_toy: '#dc2626',
  vizdoom_duel: '#c084fc',
};

// Per-game seat labels (seat 0, seat 1). Falls back to "Seat N" for other games.
const SEAT_LABELS: Record<string, [string, string]> = {
  tictactoe: ['X', 'O'],
  connect_four: ['Red', 'Yellow'],
  holdem: ['Button', 'Big Blind'],
  rag_race: ['Player 1', 'Player 2'],
  code_golf: ['Player 1', 'Player 2'],
  test_duel: ['Player 1', 'Player 2'],
  bug_hunt: ['Player 1', 'Player 2'],
  arena: ['Blue', 'Orange'],
  fighter: ['Blue', 'Orange'],
  vizdoom_toy: ['Marine 0', 'Marine 1'],
  vizdoom_duel: ['Marine 0', 'Marine 1'],
};

/** Animated ellipsis for the "thinking" state (see games.css). */
function ThinkingDots() {
  return (
    <span className="games-think-dots">
      <span>.</span>
      <span>.</span>
      <span>.</span>
    </span>
  );
}

/** Dispatch a public state to its per-game renderer (shared with the replay viewer). */
export function BoardRenderer({ board }: { board: PublicState }) {
  return board.game === 'tictactoe' ? (
    <TicTacToeBoard board={board} />
  ) : board.game === 'connect_four' ? (
    <ConnectFourBoard board={board} />
  ) : board.game === 'holdem' ? (
    <PokerBoard board={board} />
  ) : board.game === 'rag_race' ? (
    <RagRaceBoard board={board} />
  ) : board.game === 'code_golf' ? (
    <CodeGolfBoard board={board} />
  ) : board.game === 'test_duel' ? (
    <TestDuelBoard board={board} />
  ) : board.game === 'bug_hunt' ? (
    <BugHuntBoard board={board} />
  ) : board.game === 'arena' ? (
    <ArenaBoard board={board} />
  ) : board.game === 'fighter' ? (
    <FighterCanvas board={board} />
  ) : board.game === 'vizdoom_toy' || board.game === 'vizdoom_duel' ? (
    <VizDoomBoard board={board} />
  ) : (
    <pre style={{ padding: '0.5rem', fontSize: '0.75rem' }}>{JSON.stringify(board, null, 2)}</pre>
  );
}

/** One seat's identity card in the board header: who you're actually playing. */
function SeatBadge({
  profile,
  label,
  you,
  thinking,
  winner,
}: {
  profile: SeatProfile;
  label: string;
  you: boolean;
  thinking: boolean;
  winner: boolean;
}) {
  const name = profile.handle ?? profile.display_name;
  return (
    <div className={`games-seat-badge${thinking ? ' thinking' : ''}${winner ? ' winner' : ''}`}>
      <span className="games-seat-avatar">{profile.avatar}</span>
      <span className="games-seat-name" title={profile.account_id}>
        {name}
        {profile.is_bot ? ' 🤖' : ''}
      </span>
      <span className="games-seat-meta">
        {label}
        {profile.rating !== null ? ` · ${Math.round(profile.rating)}` : ''}
        {profile.tier ? ` · ${profile.tier}` : ''}
      </span>
      {you && <span className="games-seat-you">you</span>}
      {profile.model_label && <span className="games-seat-model">{profile.model_label}</span>}
    </div>
  );
}

/** The board with no match on it: instead of a dead sentence, say what the node
 * is actually doing (connecting / queued / ready) and offer the next step. */
function IdleBoard() {
  const { connected, connecting, queue } = useGames();
  const [games, setGames] = useState<GameCatalogEntry[]>([]);
  useEffect(() => {
    if (connected) fetchGamesCatalog().then(setGames);
  }, [connected]);

  let body;
  if (connecting) {
    body = (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem', padding: '3rem 1.5rem' }}>
        <div style={{
          width: '40px',
          height: '40px',
          borderRadius: '50%',
          border: '3px solid rgba(110, 168, 254, 0.1)',
          borderTopColor: 'var(--accent, #6ea8fe)',
          animation: 'spin 1s linear infinite'
        }} className="games-loader-spinner" />
        <span style={{ fontSize: '0.9rem', color: 'var(--text-dim)', fontWeight: 500 }}>
          Establishing connection to game server<ThinkingDots />
        </span>
      </div>
    );
  } else if (queue) {
    body = (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1.2rem', padding: '3rem 1.5rem' }}>
        <div className="games-radar-scan" style={{ width: '70px', height: '70px' }}>
          <div className="games-radar-line" />
        </div>
        <div style={{ textAlign: 'center' }}>
          <span style={{ fontSize: '1rem', fontWeight: 800, color: '#c084fc', display: 'block', marginBottom: '0.2rem' }}>
            Searching for Opponent
          </span>
          <span style={{ fontSize: '0.82rem', color: 'var(--text-dim)' }}>
            Ranked Queue · {queue.gameId} ({queue.difficulty}) · <strong>{queue.waitingS}s</strong>
          </span>
        </div>
        <button
          type="button"
          className="games-sidebar-profile-btn"
          style={{ width: 'auto', paddingLeft: '1.5rem', paddingRight: '1.5rem', borderColor: 'var(--danger, #e5534b)', color: 'var(--danger, #e5534b)' }}
          onClick={() => gamesQueueLeave()}
        >
          Cancel Queue
        </button>
      </div>
    );
  } else if (connected) {
    body = (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.2rem', width: '100%', maxWidth: '560px', margin: '0 auto', padding: '1rem' }}>
        <div style={{ borderBottom: '1px solid var(--border)', paddingBottom: '0.6rem' }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 800, color: 'primary.main', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            ⚔️ Launch Practice Console
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Launch a local self-play practice match immediately to test your agent's strategy.
          </Typography>
        </div>
        
        {games.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>
            Loading available games...
          </Typography>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: '0.6rem' }}>
            {games.map((g) => {
              const accent = GAME_ACCENT[g.id] ?? 'var(--accent, #6ea8fe)';
              return (
                <button
                  key={g.id}
                  type="button"
                  className="games-console-tile"
                  style={{
                    background: 'rgba(255, 255, 255, 0.01)',
                    border: '1px solid var(--border)',
                    borderRadius: '8px',
                    padding: '0.8rem 0.5rem',
                    cursor: 'pointer',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: '0.5rem',
                    color: 'var(--text)',
                    transition: 'all 0.15s ease',
                    '--tile-accent': accent,
                  } as React.CSSProperties}
                  onClick={() => void playVsOwnAgent(g.id)}
                >
                  <span style={{ fontSize: '1.6rem' }}>{GAME_ICONS[g.id] ?? '🎲'}</span>
                  <span style={{ fontSize: '0.78rem', fontWeight: 700, textAlign: 'center' }}>{g.name}</span>
                </button>
              );
            })}
          </div>
        )}
        
        <button
          type="button"
          className="games-sidebar-profile-btn"
          onClick={() => registry.openPanel('games.lobby')}
          style={{ padding: '0.45rem', alignSelf: 'center', width: 'auto', paddingLeft: '2rem', paddingRight: '2rem' }}
        >
          Open Games Lobby
        </button>
      </div>
    );
  } else {
    body = (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1.2rem', padding: '3rem 1.5rem', maxWidth: '400px', margin: '0 auto', textAlign: 'center' }}>
        <div style={{ fontSize: '3rem', opacity: 0.35 }}>🖥️</div>
        <div>
          <span style={{ fontSize: '0.95rem', fontWeight: 800, color: 'var(--text)', display: 'block', marginBottom: '0.4rem' }}>
            Console Standby
          </span>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-dim)', lineHeight: 1.5, display: 'block' }}>
            No game match is currently active. Connect and select a game in the Games lobby to start playing.
          </span>
        </div>
        <button
          type="button"
          className="games-sidebar-profile-btn"
          onClick={() => registry.openPanel('games.lobby')}
          style={{ padding: '0.45rem 1.5rem', width: 'auto' }}
        >
          Open Games Lobby
        </button>
      </div>
    );
  }

  return (
    <div style={{
      height: '100%',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'radial-gradient(circle at center, rgba(30, 30, 36, 0.3) 0%, rgba(15, 15, 18, 0.7) 100%)',
    }}>
      {body}
    </div>
  );
}

/** The live board. Dispatches to a per-game renderer by `board.game`. Spectator
 * view — you watch your agent play. */
export function GameBoardPanel() {
  const {
    board,
    over,
    thinkingSeat,
    gameId,
    matchSeats,
    replayId,
    matchStartedAt,
    accountId,
    tableId,
    series,
  } = useGames();
  // The VS intro splash: shown for the first moments after `match_info`, derived
  // from the store's wall-clock timestamp — NOT a mount effect, so a tab-switch
  // remount mid-match doesn't replay it.
  const SPLASH_MS = 2200;
  const [, bump] = useState(0); // re-render once when the splash window closes
  useEffect(() => {
    if (matchStartedAt === null) return;
    const remaining = matchStartedAt + SPLASH_MS - Date.now();
    if (remaining <= 0) return;
    const t = setTimeout(() => bump((n) => n + 1), remaining);
    return () => clearTimeout(t);
  }, [matchStartedAt]);
  const splash = matchStartedAt !== null && Date.now() - matchStartedAt < SPLASH_MS;

  if (!board) return <IdleBoard />;

  const seat = (n: number | null | undefined): string => {
    if (n === null || n === undefined) return '—';
    const who = matchSeats?.[n];
    if (who) return who.handle ?? who.display_name;
    return SEAT_LABELS[board.game]?.[n] ?? `Seat ${n}`;
  };

  const mySeat = matchSeats?.findIndex((p) => accountId !== null && p.account_id === accountId);
  const iWon = over !== null && mySeat !== undefined && mySeat >= 0 && over.winner === mySeat;

  // Why nothing is moving right now: whose turn / who's thinking (with the model
  // that's doing the thinking), plus the game's phase (grading, simulating, …).
  const phase = over ? null : phaseLabel(board);
  const myThinking = thinkingSeat !== null && mySeat !== undefined && thinkingSeat === mySeat;
  const thinkingModel = myThinking ? matchSeats?.[thinkingSeat]?.model_label : null;
  const banner = over ? (
    over.winner === null ? (
      '🤝 Draw'
    ) : (
      `🏆 ${seat(over.winner)} wins!`
    )
  ) : thinkingSeat !== null ? (
    <>
      {seat(thinkingSeat)}
      {thinkingModel ? ` (${thinkingModel})` : ''} is thinking
      <ThinkingDots />
    </>
  ) : (
    `Turn: ${seat(board.turn)}`
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', position: 'relative' }}>
      {splash && matchSeats && matchSeats.length >= 2 && (
        <div className="games-vs-splash">
          <span className="games-vs-side left">
            {matchSeats[0].avatar} {matchSeats[0].handle ?? matchSeats[0].display_name}
          </span>
          <span className="games-vs-mark">VS</span>
          <span className="games-vs-side right">
            {matchSeats[1].avatar} {matchSeats[1].handle ?? matchSeats[1].display_name}
          </span>
        </div>
      )}
      {iWon && (
        <div className="games-confetti" aria-hidden>
          {Array.from({ length: 18 }, (_, i) => (
            <span key={i} style={{ ['--i' as string]: i }} />
          ))}
        </div>
      )}
      {matchSeats && (
        <div className="games-seat-row">
          {matchSeats.map((p, i) => (
            <SeatBadge
              key={i}
              profile={p}
              label={SEAT_LABELS[board.game]?.[i] ?? `Seat ${i}`}
              you={accountId !== null && p.account_id === accountId}
              thinking={!over && thinkingSeat === i}
              winner={over?.winner === i}
            />
          ))}
        </div>
      )}
      <div
        className={over ? 'games-winner-banner' : undefined}
        style={{
          padding: '0.35rem 0.6rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.8rem',
          color: over ? 'var(--accent, #6ea8fe)' : 'var(--text-dim)',
          fontWeight: over ? 700 : 400,
          display: 'flex',
          alignItems: 'center',
          gap: '0.6rem',
        }}
      >
        <span>
          {gameId ?? 'game'} · {banner}
          {phase ? ` · ${phase}` : ''}
        </span>
        {myThinking && (
          <button
            type="button"
            style={{ fontSize: '0.7rem' }}
            // From a standalone board pane this opens the hub host rather than
            // docking thoughts alongside — pre-existing revealRegionView behavior.
            onClick={() => revealRegionView('games.thoughts')}
          >
            watch reasoning →
          </button>
        )}
        {series && (
          <span
            className="games-series-pips"
            title={`best of ${series.best_of} — game ${series.game_index + 1} next`}
          >
            🏁 {series.wins.join('–')}
          </span>
        )}
        {over && replayId && (
          <button type="button" onClick={() => openReplay(replayId)}>
            📼 Watch replay
          </button>
        )}
        {over && tableId && !series && (
          <button
            type="button"
            onClick={() => rematchOffer(tableId)}
            title="Offer the same terms again"
          >
            🔁 Rematch
          </button>
        )}
      </div>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <BoardRenderer board={board} />
      </div>
    </div>
  );
}
