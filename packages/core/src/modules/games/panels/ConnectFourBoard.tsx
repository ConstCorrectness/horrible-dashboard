import type { PublicState } from '../game-ws';

// Disc colors for the two seats (Red moves first, then Yellow).
const DISC: Record<string, string> = {
  R: 'var(--danger, #e5534b)',
  Y: 'var(--warning, #d29922)',
};

/** Renders a Connect Four board from the server's public state. The board arrives
 * top row first (`board[0]` is the top), so it maps straight onto the grid. Spectator
 * only — discs are dropped by the agents, not by clicking. */
export function ConnectFourBoard({ board }: { board: PublicState }) {
  const cols = typeof board.cols === 'number' ? board.cols : 7;
  const grid = (board.board as (string | null)[][] | undefined) ?? [];

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${cols}, 44px)`,
        gap: '4px',
        margin: '0.5rem auto',
        width: 'max-content',
        padding: '8px',
        background: 'var(--accent, #6ea8fe)',
        borderRadius: '8px',
      }}
    >
      {grid.flatMap((row, r) =>
        row.map((cell, c) => (
          <div
            key={`${r}-${c}`}
            style={{
              width: 44,
              height: 44,
              borderRadius: '50%',
              background: 'var(--bg, #0d1117)',
              boxShadow: 'inset 0 2px 4px rgba(0,0,0,0.4)',
              overflow: 'hidden',
            }}
          >
            {/* Keyed by disc so a newly-dropped piece remounts and falls in. */}
            {cell && (
              <div
                key={`${r}-${c}-${cell}`}
                className="games-disc-drop"
                style={{
                  width: '100%',
                  height: '100%',
                  borderRadius: '50%',
                  background: DISC[cell],
                  boxShadow: 'inset 0 2px 4px rgba(0,0,0,0.4)',
                }}
              />
            )}
          </div>
        )),
      )}
    </div>
  );
}
