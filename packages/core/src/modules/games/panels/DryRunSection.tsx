import { useState, type CSSProperties } from 'react';

import { apiPost } from '../../../api';
import type { TraceStep } from '../game-ws';
import { TraceRow } from './TraceRow';

/**
 * The harness editor's **full-loop tester**: run the WHOLE draft loadout —
 * context + every tool + the real model — through one agent turn against a
 * sample engine position, and show the complete reasoning trace. Unlike the
 * per-tool Test button (one tool, hand-written observation) this exercises
 * exactly what a live match runs, minus the fallback that would hide failures.
 */

interface DryRunStep {
  kind: TraceStep['kind'];
  t_ms: number;
  content?: string | null;
  tool_calls?: { name: string; arguments: string }[];
  name?: string | null;
  result?: string | null;
  action_id?: string | null;
}

interface DryRunResponse {
  ok: boolean;
  error: string | null;
  observation: Record<string, unknown>;
  legal_actions: { id: string; label?: string }[];
  compile_errors: Record<string, string>;
  steps: DryRunStep[];
  chosen: string | null;
  rounds_used: number;
  total_ms: number;
}

const dimStyle: CSSProperties = { color: 'var(--text-dim)', fontSize: '0.72rem' };

export function DryRunSection({
  gameId,
  loadout,
  engineGames,
}: {
  gameId: string;
  /** The panel's current draft (unsaved edits included) — sent as-is. */
  loadout: { context: string; tools: unknown[]; model: unknown };
  engineGames: { id: string; name: string }[];
}) {
  // `default`/`town` loadouts have no engine of their own — pick a sample game.
  const isEngineGame = engineGames.some((g) => g.id === gameId);
  const [sampleGame, setSampleGame] = useState<string | null>(null);
  const game = sampleGame ?? (isEngineGame ? gameId : 'tictactoe');
  const [seed, setSeed] = useState(0);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<DryRunResponse | null>(null);

  const run = async () => {
    setRunning(true);
    setResult(null);
    try {
      const r = await apiPost<DryRunResponse>('/games/dry-run', {
        game_id: game,
        loadout: { ...loadout, game_id: game },
        seed,
      });
      setResult(r);
    } catch (e) {
      setResult({
        ok: false,
        error: String(e),
        observation: {},
        legal_actions: [],
        compile_errors: {},
        steps: [],
        chosen: null,
        rounds_used: 0,
        total_ms: 0,
      });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: '4px',
        padding: '0.5rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.4rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <strong>Dry run</strong>
        <span style={dimStyle}>full loop: context + all tools + the real model, no match</span>
        <select value={game} onChange={(e) => setSampleGame(e.target.value)}>
          {engineGames.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
          <span style={dimStyle}>seed</span>
          <input
            type="number"
            value={seed}
            onChange={(e) => setSeed(Number(e.target.value))}
            style={{ width: '4rem' }}
          />
        </label>
        <button type="button" onClick={() => void run()} disabled={running}>
          {running ? 'running…' : '▶ Dry run'}
        </button>
      </div>

      {result && (
        <>
          {!result.ok && (
            <div style={{ color: '#e5534b' }}>⚠ {result.error ?? 'dry run failed'}</div>
          )}
          {Object.entries(result.compile_errors).map(([name, err]) => (
            <div key={name} style={{ color: '#e5a13f' }}>
              ⚠ tool <code>{name}</code>: {err}
            </div>
          ))}
          {result.legal_actions.length > 0 && (
            <details>
              <summary style={dimStyle}>
                sample position · {result.legal_actions.length} legal actions
              </summary>
              <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap', margin: '0.3rem 0' }}>
                {result.legal_actions.map((a) => (
                  <span key={a.id} className="games-tier-chip">
                    {a.label ?? a.id}
                  </span>
                ))}
              </div>
              <pre
                style={{
                  fontSize: '0.7rem',
                  maxHeight: '10rem',
                  overflow: 'auto',
                  margin: 0,
                }}
              >
                {JSON.stringify(result.observation, null, 2)}
              </pre>
            </details>
          )}
          {result.steps.length > 0 && (
            <details className="games-dryrun-trace">
              <summary style={dimStyle}>
                {result.ok
                  ? result.chosen !== null
                    ? `chose ${result.chosen}`
                    : 'never committed a move within the round budget'
                  : 'partial trace'}
                {' · '}
                {result.rounds_used}/6 rounds · {Math.round(result.total_ms)}ms · show trace
              </summary>
              <div style={{ fontSize: '0.8rem' }}>
                {result.steps.map((s, i) => (
                  <TraceRow
                    key={i}
                    step={s as TraceStep}
                    suffix={<span style={dimStyle}> +{Math.round(s.t_ms)}ms</span>}
                  />
                ))}
              </div>
            </details>
          )}
          {result.ok && result.steps.length === 0 && (
            <div style={dimStyle}>
              {result.chosen !== null ? `chose ${result.chosen}` : 'no trace steps'} ·{' '}
              {Math.round(result.total_ms)}ms
            </div>
          )}
        </>
      )}
    </div>
  );
}
