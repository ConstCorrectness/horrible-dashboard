import { useCallback, useEffect, useState } from 'react';

import { Button } from '../../../Primitives';
import { registry } from '../../../registry';
import { adapt, listDatasets, type AdaptResult, type Dataset } from '../api';
import { FormatVerdict } from './FormatVerdict';

/**
 * Pick a registered dataset for a task, and refuse the ones that cannot train it.
 *
 * Mounted as a region on the recipe pane. It replaces a bare text input whose
 * three failure modes were all silent: a column that does not exist, a column
 * holding a nested structure, and a preference dataset feeding an SFT objective.
 *
 * The refusal is the feature. `datasets.adapt` answers "can this task eat this
 * shape" against real rows, and when the answer is no this shows the reason
 * rather than greying out a row with no explanation — "this is preference data
 * and SFT has no way to use the rejected answer" is actionable; a disabled item
 * is not.
 */

const dim = { color: 'var(--text-dim)' } as const;
const mono = { fontFamily: 'var(--font-mono, monospace)' } as const;

export function DatasetPicker({
  task = 'sft',
  selectedId = '',
  onPick,
}: {
  task?: string;
  selectedId?: string;
  onPick?: (dataset: Dataset, result: AdaptResult) => void;
}) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [checking, setChecking] = useState('');
  const [result, setResult] = useState<AdaptResult | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    listDatasets()
      .then(setDatasets)
      .catch((e: Error) => setError(e.message));
  }, []);

  const check = useCallback(
    (dataset: Dataset) => {
      setChecking(dataset.id);
      setResult(null);
      setError('');
      adapt({ dataset_id: dataset.id, task })
        .then((r) => {
          setResult(r);
          if (r.adaptation.ok) onPick?.(dataset, r);
        })
        .catch((e: Error) => setError(e.message))
        .finally(() => setChecking(''));
    },
    [task, onPick],
  );

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        padding: '0.5rem 0.6rem',
        minHeight: 0,
        overflow: 'auto',
      }}
    >
      <div
        style={{
          fontWeight: 700,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          fontSize: '0.68rem',
          color: 'var(--text-secondary)',
        }}
      >
        Dataset for {task}
      </div>

      {error && <div style={{ color: 'var(--danger)', fontSize: '0.75rem' }}>{error}</div>}

      {datasets.length === 0 ? (
        <div style={{ ...dim, fontSize: '0.75rem', lineHeight: 1.5 }}>
          No datasets registered yet.{' '}
          <Button intent="ghost" onClick={() => registry.openPanel('datasets.browser')}>
            Open the browser
          </Button>{' '}
          to find one — or register the SFT export your evals already wrote.
        </div>
      ) : (
        datasets.map((d) => {
          const selected = d.id === selectedId;
          return (
            <button
              key={d.id}
              className="hd-row"
              onClick={() => check(d)}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'flex-start',
                gap: '0.2rem',
                textAlign: 'left',
                cursor: 'pointer',
                background: 'transparent',
                border: '1px solid var(--border)',
                borderLeft: `2px solid ${selected ? 'var(--accent)' : 'transparent'}`,
                borderRadius: 'var(--radius-lg)',
                padding: '0.4rem 0.5rem',
                color: 'inherit',
                width: '100%',
              }}
            >
              <span style={{ fontSize: '0.78rem' }}>{d.name}</span>
              <span style={{ ...dim, ...mono, fontSize: '0.68rem' }}>
                {d.source} · {d.split} · {d.format}
                {checking === d.id ? ' · checking…' : ''}
              </span>
            </button>
          );
        })
      )}

      {result && (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)',
            padding: '0.45rem 0.55rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.35rem',
          }}
        >
          <FormatVerdict detection={result.detection} compact />
          {result.adaptation.ok ? (
            <div style={{ fontSize: '0.75rem', color: 'var(--ok)' }}>
              Trainable by {task}
              {result.textField ? ` — trl reads \`${result.textField}\`` : ''}
              {result.adaptation.needsFormatting
                ? '. A reshaping cell will be generated into the notebook.'
                : '.'}
            </div>
          ) : (
            <div style={{ fontSize: '0.75rem', color: 'var(--danger)', lineHeight: 1.45 }}>
              {result.adaptation.problem}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
