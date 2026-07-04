import type { PublicState } from '../game-ws';

/** Renders a tic-tac-toe board from the server's public state. Spectator-only —
 * moves come from the agents, not clicks. */
export function TicTacToeBoard({ board }: { board: PublicState }) {
  const cells = board.board ?? Array<string | null>(9).fill(null);
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 64px)',
        gridTemplateRows: 'repeat(3, 64px)',
        gap: '4px',
        margin: '0.5rem auto',
        width: 'max-content',
      }}
    >
      {cells.map((c, i) => (
        <div
          key={i}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '2rem',
            fontWeight: 700,
            background: 'var(--surface, #1c1c1c)',
            border: '1px solid var(--border)',
            borderRadius: '4px',
            color: c === 'X' ? 'var(--accent, #6ea8fe)' : 'var(--text)',
          }}
        >
          {c ?? ''}
        </div>
      ))}
    </div>
  );
}
