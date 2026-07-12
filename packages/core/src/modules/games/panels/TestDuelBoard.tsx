import { type PublicState } from '../game-ws';

interface DuelReport {
  holds: boolean;
  valid_tests: boolean;
  kills: boolean;
  score: number;
}

/** Test Duel: spec + phase progress live; post-game reveal of both impls, both
 * suites, and the holds/valid/kills verdicts. */
export function TestDuelBoard({ board }: { board: PublicState }) {
  const phase = String(board.phase ?? 'impl');
  const impls = (board.submitted_impls as boolean[]) ?? [false, false];
  const tests = (board.submitted_tests as boolean[]) ?? [false, false];
  const grading = Boolean(board.grading);
  const reports = board.reports as DuelReport[] | undefined;
  const implCode = board.impls as string[] | undefined;
  const testCode = board.tests as string[] | undefined;

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
        <strong>⚖️ {String(board.spec ?? '')}</strong>
      </div>
      <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
        <span className="games-tier-chip">phase: {grading ? 'grading' : phase}</span>
        {[0, 1].map((i) => (
          <span key={i} className="games-tier-chip">
            seat {i}: impl {impls[i] ? '✓' : '…'} · tests {tests[i] ? '✓' : '…'}
          </span>
        ))}
        {grading && <span className="games-tier-chip">🧪 running all suites…</span>}
      </div>
      {reports && (
        <div style={{ display: 'flex', gap: '0.6rem' }}>
          {reports.map((r, i) => (
            <div
              key={i}
              style={{
                flex: 1,
                minWidth: 0,
                border: '1px solid var(--border)',
                borderRadius: 8,
                padding: '0.5rem',
              }}
            >
              <div
                style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}
              >
                <strong>
                  seat {i} — {r.score} pts
                </strong>
                <span>{r.holds ? '🛡 impl holds' : '💥 impl broken'}</span>
                <span>{r.valid_tests ? '✅ valid tests' : '⚠️ invalid tests'}</span>
                <span>{r.kills ? '🗡 killed opponent' : '— no kill'}</span>
              </div>
              {implCode && (
                <pre
                  style={{
                    fontSize: '0.72rem',
                    overflow: 'auto',
                    maxHeight: '9rem',
                    border: '1px solid var(--border)',
                    borderRadius: 6,
                    padding: '0.4rem',
                  }}
                >
                  {`# impl\n${implCode[i] || '(empty)'}`}
                </pre>
              )}
              {testCode && (
                <pre
                  style={{
                    fontSize: '0.72rem',
                    overflow: 'auto',
                    maxHeight: '9rem',
                    border: '1px solid var(--border)',
                    borderRadius: 6,
                    padding: '0.4rem',
                  }}
                >
                  {`# tests\n${testCode[i] || '(empty)'}`}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
