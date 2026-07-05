import { useGames } from '../game-ws';
import { ConnectFourBoard } from './ConnectFourBoard';
import { TicTacToeBoard } from './TicTacToeBoard';

// Per-game seat labels (seat 0, seat 1). Falls back to "Seat N" for other games.
const SEAT_LABELS: Record<string, [string, string]> = {
  tictactoe: ['X', 'O'],
  connect_four: ['Red', 'Yellow'],
};

/** The live board. Dispatches to a per-game renderer by `board.game`. Spectator
 * view — you watch your agent play. */
export function GameBoardPanel() {
  const { board, over, thinkingSeat, gameId } = useGames();

  if (!board) {
    return (
      <div style={{ padding: '1rem', color: 'var(--text-dim)', fontSize: '0.85rem' }}>
        No active game. Open the Games lobby and start a match to watch your agent play.
      </div>
    );
  }

  const seat = (n: number | null | undefined): string =>
    n === null || n === undefined ? '—' : (SEAT_LABELS[board.game]?.[n] ?? `Seat ${n}`);

  const banner = over
    ? over.winner === null
      ? 'Draw'
      : `${seat(over.winner)} wins`
    : thinkingSeat !== null
      ? `${seat(thinkingSeat)} is thinking…`
      : `Turn: ${seat(board.turn)}`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          padding: '0.35rem 0.6rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.8rem',
          color: over ? 'var(--accent, #6ea8fe)' : 'var(--text-dim)',
          fontWeight: over ? 700 : 400,
        }}
      >
        {gameId ?? 'game'} · {banner}
      </div>
      <div style={{ flex: 1, overflow: 'auto' }}>
        {board.game === 'tictactoe' ? (
          <TicTacToeBoard board={board} />
        ) : board.game === 'connect_four' ? (
          <ConnectFourBoard board={board} />
        ) : (
          <pre style={{ padding: '0.5rem', fontSize: '0.75rem' }}>
            {JSON.stringify(board, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
