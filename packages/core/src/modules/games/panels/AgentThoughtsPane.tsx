import { useEffect, useRef, type ReactNode } from 'react';

import { useGames, type TraceStep } from '../game-ws';

const KIND_ICON: Record<string, string> = {
  assistant: '💬',
  tool_result: '📥',
  chose: '✅',
  fallback: '🎲',
};

/** One reasoning step, rendered the same wherever a trace appears (this live
 * pane, and the harness editor's dry-run tester). */
export function TraceRow({ step, suffix }: { step: TraceStep; suffix?: ReactNode }) {
  return (
    <div className="games-trace-step" data-kind={step.kind}>
      <span className="games-trace-icon">{KIND_ICON[step.kind] ?? '·'}</span>
      <div className="games-trace-body">
        {step.kind === 'assistant' && (
          <>
            {step.content && <div>{step.content}</div>}
            {(step.tool_calls ?? []).map((c, i) => (
              <div key={i} className="games-trace-call">
                🔧 {c.name}({c.arguments})
              </div>
            ))}
          </>
        )}
        {step.kind === 'tool_result' && (
          <div className="games-trace-call">
            {step.name} → {step.result}
          </div>
        )}
        {step.kind === 'chose' && <div>committed {step.action_id}</div>}
        {step.kind === 'fallback' && (
          <div style={{ color: 'var(--text-dim)' }}>
            harness failed — random fallback played {step.action_id}
          </div>
        )}
        {suffix}
      </div>
    </div>
  );
}

/**
 * Your own agent's live reasoning during a match: every model turn, tool call,
 * and tool result, streamed as it thinks. Opponents never appear here — their
 * trajectory is revealed in the post-match replay.
 */
export function AgentThoughtsPane() {
  const { trace, matchSeats } = useGames();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [trace.length]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        fontSize: '0.8rem',
      }}
    >
      <div
        style={{
          padding: '0.35rem 0.6rem',
          borderBottom: '1px solid var(--border)',
          color: 'var(--text-dim)',
        }}
      >
        💭 Agent Thoughts {matchSeats ? '· live' : ''}
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '0.4rem 0.6rem' }}>
        {trace.length === 0 ? (
          <div style={{ color: 'var(--text-dim)' }}>
            Your agent's reasoning streams here while it plays. Nothing yet — start a match (and set
            the <code>games.policy</code> setting to <code>agent</code> so a model, not the random
            policy, picks moves).
          </div>
        ) : (
          trace.map((entry) => <TraceRow key={entry.idx} step={entry.step} />)
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
