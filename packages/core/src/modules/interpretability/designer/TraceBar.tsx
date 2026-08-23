/**
 * The second importer, as a control: point at an `nn.Module` and get its real
 * topology back as an editable design.
 *
 * It shares the project picker with the probe rather than adding a second one —
 * both need the same venv, and asking twice for one answer is a question the user
 * has already answered.
 *
 * What it renders after a trace is the part that matters. A trace is in torch's
 * vocabulary, not ours, so some of it will not map; the placeholders are **named**
 * and the fact that they raise is stated, because an import that reported only
 * "traced ✓" would be handing you a sketch and calling it the model.
 */
import { useState } from 'react';

import type { TraceResult } from './graph';

export function TraceBar({
  project,
  onTrace,
  tracing,
  result,
}: {
  project: string;
  onTrace: (target: string) => void;
  tracing: boolean;
  result: TraceResult | null;
}) {
  const [target, setTarget] = useState('');

  return (
    <div className="mg-probe">
      <div className="mg-probe-head">
        <span className="mg-probe-title">Trace</span>
        <input
          className="mg-input mg-probe-pick"
          placeholder="package.module.ClassName"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          aria-label="Module to trace"
        />
        <button
          type="button"
          className="mg-button"
          onClick={() => onTrace(target)}
          disabled={tracing || !project || !target.trim()}
          title={
            project
              ? 'Import the real topology of a module in this project, traced by torch.fx.'
              : 'Pick a training project first.'
          }
        >
          {tracing ? 'tracing…' : 'Import'}
        </button>
      </div>

      {!result && (
        <p className="mg-probe-note">
          Replaces the canvas with the traced module. Operations with no matching node type arrive
          as placeholders that <strong>raise</strong> — a pass-through stub would train fine and be
          a different model.
        </p>
      )}

      {result && (
        <div
          className={`mg-probe-out ${
            result.status === 'traced'
              ? 'mg-probe-ok'
              : result.status === 'failed'
                ? 'mg-probe-bad'
                : 'mg-probe-unknown'
          }`}
        >
          <span className="mg-probe-line">
            {result.status === 'unavailable' && <strong>Not traced. </strong>}
            {result.message}
          </span>
          {result.placeholders.length > 0 && (
            <span className="mg-probe-line mg-probe-note">
              Placeholders: <span className="mg-mono">{result.placeholders.join(', ')}</span>
            </span>
          )}
          {result.traceback && <pre className="mg-probe-trace">{result.traceback}</pre>}
        </div>
      )}
    </div>
  );
}
