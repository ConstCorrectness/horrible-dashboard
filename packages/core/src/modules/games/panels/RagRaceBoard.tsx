import type { PublicState } from '../game-ws';

interface RaceResult {
  id: string;
  prompt: string;
  accept: string[];
  answers: [string, string];
  correct: [boolean, boolean];
}

const SEAT_NAMES = ['Player 1', 'Player 2'];

/**
 * The RAG race board. During the race: both lanes with a live "racing / submitted"
 * status (the server broadcasts progress as each seat submits). After: the report
 * card — per-question answers, ✓/✗ per seat, the acceptable answers revealed for
 * learning, and the final score.
 */
export function RagRaceBoard({ board }: { board: PublicState }) {
  const submitted = (board.submitted as boolean[] | undefined) ?? [false, false];
  const questions = (board.questions as { id: string; prompt: string }[] | undefined) ?? [];
  const scores = board.scores as [number, number] | undefined;
  const results = board.results as RaceResult[] | undefined;
  const winner = board.winner as number | null | undefined;
  const done = Array.isArray(scores);

  return (
    <div style={{ padding: '0.8rem', display: 'flex', flexDirection: 'column', gap: '0.7rem' }}>
      {/* Race lanes: one per seat. */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
        {[0, 1].map((seat) => (
          <div
            key={seat}
            className={`games-race-lane${
              done && winner === seat ? ' games-poker-seat--winner' : ''
            }`}
          >
            <span style={{ fontWeight: 700, minWidth: '5.5rem' }}>
              {SEAT_NAMES[seat]}
              {done && winner === seat ? ' 🏆' : ''}
            </span>
            {done ? (
              <span>
                {scores![seat]}/{questions.length} correct
              </span>
            ) : submitted[seat] ? (
              <span style={{ color: 'var(--success, #3fb950)' }}>✔ submitted</span>
            ) : (
              <span className="games-race-running">
                🏃 racing
                <span className="games-think-dots">
                  <span>.</span>
                  <span>.</span>
                  <span>.</span>
                </span>
              </span>
            )}
          </div>
        ))}
      </div>

      {/* During the race: just the prompts (both agents are answering these now). */}
      {!done && (
        <div>
          <div style={{ color: 'var(--text-dim)', marginBottom: '0.3rem', fontSize: '0.78rem' }}>
            {questions.length} questions · answers are graded server-side against a hidden key
          </div>
          <ol style={{ margin: 0, paddingLeft: '1.2rem', fontSize: '0.82rem' }}>
            {questions.map((q) => (
              <li key={q.id}>{q.prompt}</li>
            ))}
          </ol>
        </div>
      )}

      {/* Post-race report card. */}
      {done && results && (
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.78rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-dim)' }}>
              <th style={{ padding: '0.25rem 0.4rem' }}>Question</th>
              <th style={{ padding: '0.25rem 0.4rem' }}>P1</th>
              <th style={{ padding: '0.25rem 0.4rem' }}>P2</th>
              <th style={{ padding: '0.25rem 0.4rem' }}>Answer key</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '0.25rem 0.4rem' }}>{r.prompt}</td>
                {[0, 1].map((seat) => (
                  <td
                    key={seat}
                    title={r.answers[seat] || '(no answer)'}
                    style={{
                      padding: '0.25rem 0.4rem',
                      color: r.correct[seat] ? 'var(--success, #3fb950)' : 'var(--text-dim)',
                    }}
                  >
                    {r.correct[seat] ? '✓' : '✗'}
                  </td>
                ))}
                <td style={{ padding: '0.25rem 0.4rem', color: 'var(--text-dim)' }}>
                  {r.accept[0]}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
