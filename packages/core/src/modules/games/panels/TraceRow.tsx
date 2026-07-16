import { type ReactNode } from 'react';

import { type TraceStep } from '../game-ws';

const KIND_ICON: Record<string, string> = {
  assistant: '💬',
  tool_result: '📥',
  chose: '✅',
  fallback: '🎲',
};

/** One agent reasoning step, rendered the same wherever a trace appears — the
 * Games Log's `agent` stream and the builder's dry-run tester. (This used to live
 * in AgentThoughtsPane, which the Games Log replaced.) */
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
