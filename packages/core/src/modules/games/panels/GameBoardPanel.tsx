import { useEffect, useState } from 'react';

import { rematchOffer, useGames, type PublicState, type SeatProfile } from '../game-ws';
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

/** The live board. Dispatches to a per-game renderer by `board.game`. Spectator
 * view — you watch your agent play. */
export function GameBoardPanel() {
  const { board, over, thinkingSeat, gameId, matchSeats, replayId, accountId, tableId, series } =
    useGames();
  // The VS intro splash: pops when a fresh match (new replay id) arrives.
  const [splash, setSplash] = useState(false);
  useEffect(() => {
    if (!replayId) return;
    setSplash(true);
    const t = setTimeout(() => setSplash(false), 2200);
    return () => clearTimeout(t);
  }, [replayId]);

  if (!board) {
    return (
      <div style={{ padding: '1rem', color: 'var(--text-dim)', fontSize: '0.85rem' }}>
        No active game. Open the Games lobby and start a match to watch your agent play.
      </div>
    );
  }

  const seat = (n: number | null | undefined): string => {
    if (n === null || n === undefined) return '—';
    const who = matchSeats?.[n];
    if (who) return who.handle ?? who.display_name;
    return SEAT_LABELS[board.game]?.[n] ?? `Seat ${n}`;
  };

  const banner = over ? (
    over.winner === null ? (
      '🤝 Draw'
    ) : (
      `🏆 ${seat(over.winner)} wins!`
    )
  ) : thinkingSeat !== null ? (
    <>
      {seat(thinkingSeat)} is thinking
      <ThinkingDots />
    </>
  ) : (
    `Turn: ${seat(board.turn)}`
  );

  const mySeat = matchSeats?.findIndex((p) => accountId !== null && p.account_id === accountId);
  const iWon = over !== null && mySeat !== undefined && mySeat >= 0 && over.winner === mySeat;

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
        </span>
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
