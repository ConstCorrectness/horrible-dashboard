import { type PublicState } from '../game-ws';

interface Attempt {
  green: boolean;
  passed: number;
  failed: number;
}

/** Bug Hunt: the shared broken repo + per-seat attempt bars live, then the fix
 * reveal once someone greens (or the clock runs out). */
export function BugHuntBoard({ board }: { board: PublicState }) {
  const attempts = (board.attempts as Attempt[][]) ?? [[], []];
  const grading = Boolean(board.grading);
  const winner = board.winner as number | null | undefined;
  const files = board.files as Record<string, string> | undefined;
  const winningFiles = board.winning_files as Record<string, string> | undefined;
  const terminal = winner !== undefined;

  return (
    <div
      style={{
        padding: '0.7rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
      }}
    >
      <div>
        <strong>🐛 {String(board.description ?? '')}</strong>
        {grading && <span style={{ color: 'var(--accent, #6ea8fe)' }}> · 🧪 verifying a fix…</span>}
      </div>

      <div style={{ display: 'flex', gap: '0.7rem' }}>
        {[0, 1].map((seat) => {
          const list = attempts[seat] ?? [];
          const last = list[list.length - 1];
          return (
            <div
              key={seat}
              style={{
                flex: 1,
                border: `1px solid ${winner === seat ? '#3fb950' : 'var(--border)'}`,
                borderRadius: 8,
                padding: '0.5rem',
              }}
            >
              <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                <strong>seat {seat}</strong>
                {winner === seat && <span>🏆 fixed it</span>}
                <span style={{ color: 'var(--text-dim)' }}>{list.length} attempts</span>
              </div>
              <div style={{ display: 'flex', gap: 3, marginTop: '0.3rem', flexWrap: 'wrap' }}>
                {list.map((a, i) => (
                  <span
                    key={i}
                    title={`${a.passed} passed / ${a.failed} failed`}
                    style={{
                      width: 14,
                      height: 14,
                      borderRadius: 3,
                      background: a.green ? '#3fb950' : '#e5534b',
                    }}
                  />
                ))}
              </div>
              {last && (
                <div style={{ color: 'var(--text-dim)', fontSize: '0.75rem', marginTop: '0.2rem' }}>
                  last: {last.passed}✓ / {last.failed}✗
                </div>
              )}
            </div>
          );
        })}
      </div>

      {terminal && files && (
        <div style={{ display: 'flex', gap: '0.6rem' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: 'var(--text-dim)' }}>Broken repo</div>
            {Object.entries(files).map(([name, content]) => (
              <details key={name}>
                <summary style={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>{name}</summary>
                <pre style={{ fontSize: '0.72rem', overflow: 'auto', maxHeight: '14rem' }}>
                  {content}
                </pre>
              </details>
            ))}
          </div>
          {winningFiles && (
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: '#3fb950' }}>Winning fix</div>
              {Object.entries(winningFiles).map(([name, content]) => (
                <details key={name} open>
                  <summary style={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>{name}</summary>
                  <pre style={{ fontSize: '0.72rem', overflow: 'auto', maxHeight: '14rem' }}>
                    {content}
                  </pre>
                </details>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
