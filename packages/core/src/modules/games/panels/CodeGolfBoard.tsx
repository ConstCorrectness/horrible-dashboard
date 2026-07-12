import { type PublicState } from '../game-ws';

interface GolfReport {
  green: boolean;
  passed: number;
  failed: number;
  bytes: number;
  output: string;
}

/** Code Golf: prompt + submission status live; full two-column reveal (solutions,
 * byte counts, hidden-test verdicts) once the server has graded. */
export function CodeGolfBoard({ board }: { board: PublicState }) {
  const submitted = (board.submitted as boolean[]) ?? [false, false];
  const grading = Boolean(board.grading);
  const reports = board.reports as GolfReport[] | undefined;
  const solutions = board.solutions as string[] | undefined;

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
        <strong>⛳ {String(board.prompt ?? '')}</strong>
        <div style={{ color: 'var(--text-dim)', fontFamily: 'monospace', fontSize: '0.78rem' }}>
          {String(board.signature ?? '')}
        </div>
      </div>
      <div style={{ display: 'flex', gap: '1rem' }}>
        {submitted.map((s, i) => (
          <span key={i} className="games-tier-chip">
            seat {i}: {s ? '📬 submitted' : '⌨️ coding…'}
          </span>
        ))}
        {grading && <span className="games-tier-chip">🧪 grading against hidden tests…</span>}
      </div>
      {reports && solutions && (
        <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'stretch' }}>
          {reports.map((r, i) => (
            <div
              key={i}
              style={{
                flex: 1,
                minWidth: 0,
                border: `1px solid ${r.green ? '#3fb950' : 'var(--border)'}`,
                borderRadius: 8,
                padding: '0.5rem',
              }}
            >
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <strong>seat {i}</strong>
                <span>{r.green ? '✅ correct' : '❌ failed hidden tests'}</span>
                <span style={{ color: 'var(--text-dim)' }}>
                  {r.bytes} bytes · {r.passed}✓/{r.failed}✗
                </span>
              </div>
              <pre
                style={{
                  fontSize: '0.72rem',
                  overflow: 'auto',
                  maxHeight: '14rem',
                  border: '1px solid var(--border)',
                  borderRadius: 6,
                  padding: '0.4rem',
                }}
              >
                {solutions[i] || '(empty submission)'}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
