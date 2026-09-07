import { useCallback, useEffect, useMemo, useState } from 'react';

import { Button, EmptyState, PaneHeader } from '../../../Primitives';
import { registry } from '../../../registry';
import { usePaneParams } from '../../../panes';
import {
  getRecipe,
  getSweep,
  previewSweep,
  saveSweep,
  startSweep,
  stopSweep,
  type RecipeField,
  type SweepAxis,
  type SweepPoint,
  type SweepRecord,
  type SweepSpec,
} from '../api';
import { ProjectsPane } from './ProjectsPane';

/**
 * Ablations: the same recipe N times with one thing changed.
 *
 * The pane exists because the alternative was editing a number, running,
 * screenshotting the chart, editing it back, and remembering — which is not an
 * experiment, it is a recollection.
 *
 * Three things it insists on, each of which was a real way to waste an afternoon:
 *
 * - **Preview before running.** The point count is the cost, and a two-axis grid
 *   is easy to misjudge. Seeing "16 runs" before starting is the difference
 *   between an ablation and a machine that is busy until Thursday.
 * - **Refusals are explained.** An axis naming a field the selected task does not
 *   have produces N identical runs and a comparison showing no effect, which
 *   reads as "this knob does nothing". The backend refuses it and says why.
 * - **The comparison is one click away.** A sweep that only produces training loss
 *   has not answered the question; the finished state links straight to the
 *   workspace where the runs are diffed by config.
 */

const dim = { color: 'var(--text-dim)' } as const;
const mono = { fontFamily: 'var(--font-mono, monospace)' } as const;

const card: React.CSSProperties = {
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-lg)',
  padding: '0.55rem 0.65rem',
  display: 'flex',
  flexDirection: 'column',
  gap: '0.45rem',
};

const heading: React.CSSProperties = {
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  fontSize: '0.68rem',
  color: 'var(--text-secondary)',
};

/** Parse a comma-separated value list, keeping numbers numeric.
 *
 * `1e-4, 2e-4` must reach the backend as floats: sent as strings they would be
 * emitted as `learning_rate='0.0001'` and the trainer would reject them several
 * minutes into the first point. */
function parseValues(text: string): unknown[] {
  return text
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      if (part === 'true') return true;
      if (part === 'false') return false;
      const numeric = Number(part);
      return Number.isFinite(numeric) && part !== '' ? numeric : part;
    });
}

function formatValues(values: unknown[]): string {
  return values.map((v) => String(v)).join(', ');
}

export function SweepPane() {
  const params = usePaneParams();
  const projectId = String(params.projectId ?? '');
  const [spec, setSpec] = useState<SweepSpec | null>(null);
  const [fields, setFields] = useState<RecipeField[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [points, setPoints] = useState<SweepPoint[]>([]);
  const [records, setRecords] = useState<SweepRecord[]>([]);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    if (!projectId) return;
    void getSweep(projectId)
      .then((data) => {
        setSpec(data.spec);
        setProblems(data.problems);
        setRecords(data.sweeps.filter((s) => s.projectId === projectId));
      })
      .catch((err) => setStatus(err instanceof Error ? err.message : String(err)));
    void getRecipe(projectId)
      .then((data) => setFields(data.fields))
      .catch(() => undefined);
  }, [projectId]);

  useEffect(load, [load]);

  // A running sweep is a long job whose progress arrives on the `training`
  // channel, but a poll is the honest fallback: the pane may be opened after the
  // sweep started, and the first render has nothing to react to.
  useEffect(() => {
    if (!records.some((r) => r.state === 'running' || r.state === 'queued')) return;
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [records, load]);

  const setAxis = useCallback(
    (index: number, next: Partial<SweepAxis>) => {
      if (!spec) return;
      const axes = spec.axes.map((a, i) => (i === index ? { ...a, ...next } : a));
      setSpec({ ...spec, axes });
    },
    [spec],
  );

  const preview = useCallback(() => {
    if (!spec) return;
    setBusy(true);
    void previewSweep(projectId, spec)
      .then((res) => {
        setProblems(res.problems);
        setPoints(res.points);
      })
      .catch((err) => setStatus(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false));
  }, [projectId, spec]);

  const run = useCallback(() => {
    if (!spec) return;
    setBusy(true);
    void saveSweep(projectId, spec)
      .then(() => startSweep(projectId, spec))
      .then(() => load())
      .catch((err) => setStatus(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false));
  }, [projectId, spec, load]);

  const numericFields = useMemo(
    () => fields.filter((f) => f.type === 'int' || f.type === 'float' || f.type === 'select'),
    [fields],
  );

  if (!projectId) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div
          style={{ ...heading, padding: '0.5rem 0.75rem', borderBottom: '1px solid var(--border)' }}
        >
          Choose a project
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <ProjectsPane />
        </div>
      </div>
    );
  }
  if (!spec) return <p style={{ ...dim, padding: '0.6rem' }}>{status || 'Loading…'}</p>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <PaneHeader
        title="Ablation sweep"
        meta={[
          <span key="p" style={{ ...dim, ...mono }}>
            {projectId}
          </span>,
        ]}
      />

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.6rem',
          padding: '0.6rem 0.7rem',
          overflow: 'auto',
          minHeight: 0,
          flex: 1,
        }}
      >
        {status && <div style={{ color: 'var(--danger)', fontSize: '0.78rem' }}>{status}</div>}

        <div style={card}>
          <div style={heading}>Axes</div>
          <p style={{ ...dim, fontSize: 11, margin: 0, lineHeight: 1.45 }}>
            Each axis is a field and the values to try. The recipe supplies everything else, so
            every point differs only in what you list here.
          </p>
          {spec.axes.map((axis, index) => (
            <div key={index} style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
              <select
                value={axis.field}
                onChange={(e) => setAxis(index, { field: e.target.value })}
                style={{ padding: '0 0.6rem', minWidth: 170 }}
              >
                <option value="">field…</option>
                {numericFields.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.label}
                  </option>
                ))}
              </select>
              <input
                type="text"
                value={formatValues(axis.values)}
                placeholder="1e-4, 2e-4, 5e-4"
                onChange={(e) => setAxis(index, { values: parseValues(e.target.value) })}
                style={{ padding: '0 0.6rem', flex: 1, minWidth: 160 }}
              />
              <Button
                intent="ghost"
                onClick={() => setSpec({ ...spec, axes: spec.axes.filter((_, i) => i !== index) })}
              >
                Remove
              </Button>
            </div>
          ))}
          <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
            <Button
              onClick={() => setSpec({ ...spec, axes: [...spec.axes, { field: '', values: [] }] })}
            >
              Add axis
            </Button>
            <select
              value={spec.strategy}
              onChange={(e) => setSpec({ ...spec, strategy: e.target.value })}
              style={{ padding: '0 0.6rem' }}
            >
              <option value="grid">grid — every combination</option>
              <option value="zip">zip — axes step together</option>
              <option value="random">random — a seeded sample</option>
            </select>
            <label
              style={{ ...dim, fontSize: 11, display: 'flex', alignItems: 'center', gap: '0.3rem' }}
            >
              parallel
              <input
                type="number"
                value={spec.maxParallel}
                min={1}
                onChange={(e) => setSpec({ ...spec, maxParallel: Number(e.target.value) || 1 })}
                style={{ padding: '0 0.6rem', width: 62 }}
              />
            </label>
          </div>
          <p style={{ ...dim, fontSize: 11, margin: 0 }}>
            One run at a time by default — two fine-tunes on one GPU is an out-of-memory error, not
            double the throughput.
          </p>
        </div>

        {problems.length > 0 && (
          <div style={{ ...card, borderColor: 'var(--danger)' }}>
            {problems.map((p) => (
              <div
                key={p}
                style={{ color: 'var(--danger)', fontSize: '0.75rem', lineHeight: 1.45 }}
              >
                {p}
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
          <Button onClick={preview} disabled={busy}>
            Preview
          </Button>
          <Button intent="primary" onClick={run} disabled={busy || problems.length > 0}>
            Run sweep
          </Button>
          {points.length > 0 && (
            <span style={{ ...dim, ...mono, fontSize: '0.72rem', alignSelf: 'center' }}>
              {points.length} runs
            </span>
          )}
        </div>

        {points.length > 0 && (
          <div style={card}>
            <div style={heading}>Points</div>
            <div style={{ ...mono, fontSize: '0.7rem', ...dim, lineHeight: 1.7 }}>
              {points.map((p) => (
                <div key={p.index}>
                  {String(p.index).padStart(2, '0')} · {p.label}
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={card}>
          <div style={heading}>Runs</div>
          {records.length === 0 ? (
            <EmptyState title="No sweeps yet">
              Add an axis, preview the points, then run. Each point records the config it exercised,
              which is what lets the workspace say which knob moved the metric.
            </EmptyState>
          ) : (
            records.map((record) => (
              <div
                key={record.sweepId}
                style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}
              >
                <div
                  style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}
                >
                  <span style={{ ...mono, fontSize: '0.72rem' }}>{record.sweepId}</span>
                  <span
                    style={{
                      fontSize: '0.7rem',
                      color:
                        record.state === 'finished'
                          ? 'var(--ok)'
                          : record.state === 'stopped'
                            ? 'var(--warn)'
                            : 'var(--accent)',
                    }}
                  >
                    {record.state}
                  </span>
                  <span style={{ ...dim, ...mono, fontSize: '0.7rem' }}>
                    {record.done}/{record.total} done
                    {record.failed ? ` · ${record.failed} failed` : ''}
                  </span>
                  {(record.state === 'running' || record.state === 'queued') && (
                    <Button
                      intent="ghost"
                      onClick={() => void stopSweep(record.sweepId).then(load)}
                    >
                      Stop
                    </Button>
                  )}
                  {record.done > 1 && (
                    <Button
                      intent="ghost"
                      onClick={() => registry.openPanel('localtrack.workspace')}
                    >
                      Compare
                    </Button>
                  )}
                </div>
                <div style={{ ...mono, fontSize: '0.68rem', ...dim, lineHeight: 1.6 }}>
                  {record.results.map((entry) => (
                    <div key={`${entry.index}-${entry.runId}`}>
                      {entry.state === 'failed' ? '✕' : entry.state === 'finished' ? '✓' : '·'}{' '}
                      {entry.label}
                      {entry.error ? ` — ${entry.error}` : ''}
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
