import { useEffect, useMemo, useState } from 'react';

import { compareRuns } from '../api';
import type { CompareResult } from '../types';

/**
 * The run × config × metric grid: which knob actually caused this.
 *
 * The single most valuable thing localtrack was missing. It could overlay twelve
 * loss curves on one chart, which tells you *that* they differ and nothing about
 * *why* — the why lives in each run's config, and nothing had ever written a
 * comparable one there until the training sweep started declaring one per point.
 *
 * Three rules keep the table readable:
 *
 * - **Only what varies is a column.** A fine-tune has forty knobs and an ablation
 *   moves one; repeating the other thirty-nine on every row buries the answer.
 *   What was held constant is reported once, underneath.
 * - **The best value in each metric column is marked.** Scanning a column of
 *   six-decimal floats for the smallest is work the table can do.
 * - **A mixed comparison says so.** Runs on different backends or tasks are still
 *   comparable, but the difference between them is not the axis you varied, and a
 *   table that stays silent about that reads as an ablation result.
 */

const dim = { color: 'var(--text-dim)' } as const;
const mono = { fontFamily: 'var(--font-mono, monospace)' } as const;

const cell: React.CSSProperties = {
  padding: '0.25rem 0.55rem',
  borderBottom: '1px solid var(--border)',
  whiteSpace: 'nowrap',
  fontSize: '0.72rem',
};

/** Lower is better for a loss; higher for accuracy and friends. */
function lowerIsBetter(key: string): boolean {
  return /loss|error|perplexity|ppl/i.test(key);
}

function show(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : value.toPrecision(4);
  }
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

export function ComparePanel({ runIds, metricKey }: { runIds: string[]; metricKey?: string }) {
  const [result, setResult] = useState<CompareResult | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!runIds.length) {
      setResult(null);
      return;
    }
    compareRuns(runIds, metricKey || '')
      .then((r) => {
        setResult(r);
        setError('');
      })
      .catch((e: Error) => setError(e.message));
  }, [runIds, metricKey]);

  const best = useMemo(() => {
    const out: Record<string, string> = {};
    if (!result) return out;
    for (const key of result.metric_keys) {
      let winner = '';
      let value = Number.NaN;
      for (const row of result.runs) {
        const candidate = row.metrics[key];
        if (candidate === undefined) continue;
        const better = Number.isNaN(value)
          ? true
          : lowerIsBetter(key)
            ? candidate < value
            : candidate > value;
        if (better) {
          value = candidate;
          winner = row.run_id;
        }
      }
      if (winner) out[key] = winner;
    }
    return out;
  }, [result]);

  if (error) return <div style={{ color: 'var(--danger)', fontSize: '0.75rem' }}>{error}</div>;
  if (!result || !result.runs.length) {
    return (
      <div style={{ ...dim, fontSize: '0.75rem', padding: '0.5rem' }}>
        Select runs in the sidebar to compare what varied between them.
      </div>
    );
  }

  const sharedKeys = Object.keys(result.shared);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', minHeight: 0 }}>
      {result.mixed.length > 0 && (
        <div style={{ fontSize: '0.72rem', color: 'var(--warn)', lineHeight: 1.45 }}>
          These runs differ in {result.mixed.map((m) => m.replace(/^_/, '')).join(' and ')}, so the
          difference between them is not the axis you varied. Comparable, but not an ablation.
        </div>
      )}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', minWidth: '100%', ...mono }}>
          <thead>
            <tr>
              <th style={{ ...cell, textAlign: 'left', color: 'var(--text-secondary)' }}>run</th>
              {result.varied.map((key) => (
                <th
                  key={key}
                  style={{ ...cell, textAlign: 'left', color: 'var(--accent)' }}
                  title="varied in this comparison"
                >
                  {key}
                </th>
              ))}
              {result.metric_keys.map((key) => (
                <th
                  key={key}
                  style={{ ...cell, textAlign: 'right', color: 'var(--text-secondary)' }}
                >
                  {key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.runs.map((row) => (
              <tr key={row.run_id}>
                <td style={{ ...cell, textAlign: 'left' }}>
                  {row.name}
                  {row.status !== 'finished' && (
                    <span style={{ ...dim, marginLeft: '0.4rem' }}>{row.status}</span>
                  )}
                </td>
                {result.varied.map((key) => (
                  <td key={key} style={{ ...cell, textAlign: 'left' }}>
                    {show(row.config[key])}
                  </td>
                ))}
                {result.metric_keys.map((key) => {
                  const won = best[key] === row.run_id;
                  return (
                    <td
                      key={key}
                      style={{
                        ...cell,
                        textAlign: 'right',
                        color: won ? 'var(--ok)' : undefined,
                        fontWeight: won ? 700 : undefined,
                      }}
                    >
                      {row.metrics[key] === undefined ? '—' : show(row.metrics[key])}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {sharedKeys.length > 0 && (
        <details style={{ fontSize: '0.7rem', ...dim }}>
          <summary style={{ cursor: 'pointer' }}>
            {sharedKeys.length} settings held constant
          </summary>
          <div style={{ ...mono, paddingTop: '0.3rem', lineHeight: 1.6 }}>
            {sharedKeys.map((k) => (
              <span key={k} style={{ marginRight: '0.8rem' }}>
                {k}={show(result.shared[k])}
              </span>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
