import { useGames } from '../game-ws';
import { TicTacToeBoard } from './TicTacToeBoard';

const MARKS = ['X', 'O'];

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

  const banner = over
    ? over.winner === null
      ? 'Draw'
      : `${MARKS[over.winner] ?? `Seat ${over.winner}`} wins`
    : thinkingSeat !== null
      ? `${MARKS[thinkingSeat] ?? `Seat ${thinkingSeat}`} is thinking…`
      : `Turn: ${board.turn !== null && board.turn !== undefined ? (MARKS[board.turn] ?? board.turn) : '—'}`;

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
        ) : (
          <pre style={{ padding: '0.5rem', fontSize: '0.75rem' }}>
            {JSON.stringify(board, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
