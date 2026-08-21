import { useCallback, useEffect, useMemo, useState } from 'react';

import { usePaneSection } from '../../layout/use-sections';
import {
  addLabel,
  compareHarnesses,
  createDataset,
  deleteRun,
  getRun,
  getStats,
  listDatasets,
  listHarnesses,
  listRuns,
  updateDataset,
  type CompareReport,
  type Dataset,
  type Harness,
  type Stats,
  type TrajectoryDetail,
  type TrajectoryRun,
  type TrajectoryStep,
} from './api';
import {
  AlertIcon,
  BrainIcon,
  CheckIcon,
  CircleIcon,
  DatabaseIcon,
  EyeIcon,
  LockIcon,
  MessageIcon,
  RecordIcon,
  RefreshIcon,
  ScaleIcon,
  TerminalIcon,
  TrashIcon,
  TrophyIcon,
  XIcon,
} from './icons';

/**
 * Trajectories: one pane, three sections — Runs, Datasets, Harness.
 *
 * One pane rather than three, per the pane-consolidation rule: these are three
 * views of one thing (the runs, where they are collected, and what configuration
 * produced them), and three panes would mean three openers and three copies of
 * "which dataset are we looking at".
 *
 * The Runs section is failure-first in the same spirit as the evals results view —
 * nobody opens a trajectory browser to admire the runs that worked. The Harness
 * section is the one that justifies the module: it is where "did my change help"
 * gets an answer, including the answer "these two never ran the same tasks, so
 * this is not a comparison".
 */

const S = {
  pane: {
    display: 'flex',
    flexDirection: 'column' as const,
    height: '100%',
    overflow: 'hidden',
    background: 'var(--bg-primary, #0d1117)',
    color: 'var(--text-primary, #c9d1d9)',
    fontSize: 13,
  },
  bar: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 12px',
    borderBottom: '1px solid var(--border, #30363d)',
    flexShrink: 0,
  },
  heading: {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.14em',
    textTransform: 'uppercase' as const,
    color: 'var(--text-secondary, #8b949e)',
  },
  mono: {
    fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace)',
    fontSize: 11,
    color: 'var(--text-secondary, #8b949e)',
  },
  body: { flex: 1, overflow: 'auto', minHeight: 0 },
  split: { display: 'flex', height: '100%', minHeight: 0 },
  list: {
    width: 340,
    flexShrink: 0,
    borderRight: '1px solid var(--border, #30363d)',
    overflow: 'auto',
  },
  detail: { flex: 1, overflow: 'auto', padding: 16, minWidth: 0 },
  input: {
    background: 'var(--bg-secondary, #161b22)',
    border: '1px solid var(--border, #30363d)',
    color: 'inherit',
    borderRadius: 4,
    padding: '4px 8px',
    fontSize: 12,
  },
  button: {
    background: 'var(--bg-secondary, #161b22)',
    border: '1px solid var(--border, #30363d)',
    color: 'inherit',
    borderRadius: 4,
    padding: '4px 10px',
    fontSize: 12,
    cursor: 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
  },
  empty: {
    padding: 32,
    textAlign: 'center' as const,
    color: 'var(--text-secondary, #8b949e)',
    fontSize: 12,
    lineHeight: 1.7,
  },
  card: {
    border: '1px solid var(--border, #30363d)',
    borderTop: '2px solid var(--accent, #58a6ff)',
    borderRadius: 3,
    padding: 12,
    background: 'var(--bg-secondary, #161b22)',
  },
};

const OUTCOME_COLOR: Record<string, string> = {
  success: 'var(--success, #3fb950)',
  failure: 'var(--danger, #f85149)',
  partial: 'var(--warning, #d29922)',
  unknown: 'var(--text-secondary, #8b949e)',
};

function outcomeColor(outcome: string | null): string {
  return outcome ? (OUTCOME_COLOR[outcome] ?? OUTCOME_COLOR.unknown) : OUTCOME_COLOR.unknown;
}

function ms(value: number | null | undefined): string {
  if (value == null) return '—';
  return value < 1000 ? `${Math.round(value)}ms` : `${(value / 1000).toFixed(1)}s`;
}

function ago(seconds: number): string {
  const delta = Date.now() / 1000 - seconds;
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

/** A number that counts up to its value.
 *
 * The one micro-interaction that earns its place here: a rate moving from 0 to
 * 62% reads as a measurement being taken.
 *
 * The timeout is not belt-and-braces — `requestAnimationFrame` does not fire in a
 * backgrounded tab, and without it the tile would sit at 0 while the real number
 * was 62. A stat that renders "0" when it means "62" is worse than no animation,
 * and it is the same mistake `analyze.py` refuses to make with an ungraded rate.
 */
function Rolling({ value, suffix = '' }: { value: number; suffix?: string }) {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    let frame = 0;
    const steps = 18;
    let handle = requestAnimationFrame(function tick() {
      frame += 1;
      setShown(value * (frame / steps));
      if (frame < steps) handle = requestAnimationFrame(tick);
      else setShown(value);
    });
    const settle = setTimeout(() => setShown(value), 600);
    return () => {
      cancelAnimationFrame(handle);
      clearTimeout(settle);
    };
  }, [value]);
  return (
    <>
      {shown.toFixed(0)}
      {suffix}
    </>
  );
}

function StepIcon({ step }: { step: TrajectoryStep }) {
  if (step.kind === 'action') {
    if (step.gated) return <LockIcon />;
    return step.ok === false ? <XIcon /> : <TerminalIcon />;
  }
  if (step.kind === 'thought') return <BrainIcon />;
  if (step.kind === 'observation') return <EyeIcon />;
  if (step.kind === 'reward') return <TrophyIcon />;
  if (step.kind === 'error') return <AlertIcon />;
  return <MessageIcon />;
}

function Json({ value }: { value: unknown }) {
  if (value == null) return null;
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return (
    <pre
      style={{
        ...S.mono,
        margin: '4px 0 0',
        padding: 8,
        background: 'var(--bg-primary, #0d1117)',
        border: '1px solid var(--border, #30363d)',
        borderRadius: 3,
        maxHeight: 220,
        overflow: 'auto',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}
    >
      {text}
    </pre>
  );
}

// --- Runs -------------------------------------------------------------------

function StepRow({ step, index }: { step: TrajectoryStep; index: number }) {
  const [open, setOpen] = useState(false);
  const failed = step.ok === false;
  return (
    <div
      style={{
        borderLeft: `2px solid ${failed ? 'var(--danger, #f85149)' : 'var(--border, #30363d)'}`,
        paddingLeft: 10,
        marginBottom: 8,
        // Staggered entrance, capped so a 200-step run does not take 20 seconds
        // to finish arriving.
        animation: `traj-in 220ms ease-out ${Math.min(index, 12) * 25}ms both`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: failed ? 'var(--danger, #f85149)' : 'inherit' }}>
          <StepIcon step={step} />
        </span>
        <span style={{ ...S.mono, minWidth: 26 }}>{step.seq}</span>
        <strong style={{ fontSize: 12 }}>{step.name ?? step.kind}</strong>
        {step.role ? <span style={S.mono}>{step.role}</span> : null}
        <span style={{ flex: 1 }} />
        {step.gated ? (
          <span style={{ ...S.mono, color: 'var(--warning, #d29922)' }}>GATED</span>
        ) : null}
        <span style={S.mono}>{ms(step.duration_ms)}</span>
        {step.args != null || step.result != null ? (
          <button style={{ ...S.button, padding: '1px 6px' }} onClick={() => setOpen(!open)}>
            {open ? 'hide' : 'data'}
          </button>
        ) : null}
      </div>
      {step.content ? (
        <div style={{ fontSize: 12, marginTop: 4, whiteSpace: 'pre-wrap', opacity: 0.9 }}>
          {step.content}
        </div>
      ) : null}
      {step.error ? (
        <div style={{ ...S.mono, color: 'var(--danger, #f85149)', marginTop: 4 }}>{step.error}</div>
      ) : null}
      {open ? (
        <div style={{ marginTop: 4 }}>
          {step.args != null ? (
            <>
              <div style={S.heading}>Arguments</div>
              <Json value={step.args} />
            </>
          ) : null}
          {step.result != null ? (
            <>
              <div style={{ ...S.heading, marginTop: 6 }}>Result</div>
              <Json value={step.result} />
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function RunDetail({ run, onChanged }: { run: TrajectoryDetail; onChanged: () => void }) {
  const grade = async (value: string) => {
    await addLabel(run.id, { key: 'outcome', value, source: 'human' });
    onChanged();
  };
  return (
    <div>
      <div style={{ ...S.card, marginBottom: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>
          {run.goal || '(no goal recorded)'}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, ...S.mono }}>
          <span>{run.id}</span>
          <span>{run.source}</span>
          <span>{run.model || '—'}</span>
          <span>{run.steps} steps</span>
          <span>{ms(run.duration_ms)}</span>
          <span style={{ color: outcomeColor(run.outcome) }}>{run.outcome ?? 'ungraded'}</span>
        </div>
        {run.harness ? (
          <div style={{ ...S.mono, marginTop: 6 }}>
            harness {run.harness}
            {run.harness_detail ? ` · ${run.harness_detail.tool_names.length} tools` : ''}
          </div>
        ) : null}
        {run.turn_id ? (
          <div style={{ ...S.mono, marginTop: 2 }}>
            turn {run.turn_id} — joins `agent_turns` for the context side
          </div>
        ) : null}
        {run.error ? (
          <div style={{ ...S.mono, color: 'var(--danger, #f85149)', marginTop: 6 }}>
            {run.error}
          </div>
        ) : null}
        <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          <span style={{ ...S.heading, alignSelf: 'center' }}>Grade</span>
          <button style={S.button} onClick={() => grade('success')}>
            <CheckIcon /> Success
          </button>
          <button style={S.button} onClick={() => grade('failure')}>
            <XIcon /> Failure
          </button>
          <button style={S.button} onClick={() => grade('partial')}>
            <CircleIcon /> Partial
          </button>
        </div>
      </div>

      {run.labels.length ? (
        <div style={{ marginBottom: 12 }}>
          <div style={S.heading}>Labels</div>
          {run.labels.map((label) => (
            <div key={label.id} style={{ ...S.mono, marginTop: 3 }}>
              {label.key} = {label.value || label.score} · {label.source}
              {label.rationale ? ` — ${label.rationale}` : ''}
            </div>
          ))}
        </div>
      ) : null}

      <div style={{ ...S.heading, marginBottom: 8 }}>Steps ({run.step_list.length})</div>
      {run.step_list.map((step, index) => (
        <StepRow key={step.seq} step={step} index={index} />
      ))}
    </div>
  );
}

function RunsSection() {
  const [runs, setRuns] = useState<TrajectoryRun[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<TrajectoryDetail | null>(null);
  const [outcome, setOutcome] = useState('');
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      const data = await listRuns({ outcome, q: query, limit: 100 });
      setRuns(data.runs);
      setTotal(data.total);
      setError('');
    } catch (err) {
      setError(String(err));
    }
  }, [outcome, query]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const open = async (id: string) => {
    try {
      setSelected(await getRun(id));
    } catch (err) {
      setError(String(err));
    }
  };

  const remove = async (id: string) => {
    await deleteRun(id);
    if (selected?.id === id) setSelected(null);
    void refresh();
  };

  return (
    <>
      <div style={S.bar}>
        <span style={S.heading}>Runs</span>
        <span style={S.mono}>{total}</span>
        <input
          style={{ ...S.input, width: 180 }}
          placeholder="search goals"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select style={S.input} value={outcome} onChange={(e) => setOutcome(e.target.value)}>
          <option value="">any outcome</option>
          <option value="failure">failure</option>
          <option value="success">success</option>
          <option value="partial">partial</option>
        </select>
        <span style={{ flex: 1 }} />
        <button style={S.button} onClick={() => void refresh()}>
          <RefreshIcon /> Refresh
        </button>
      </div>
      {error ? (
        <div style={{ ...S.mono, color: 'var(--danger, #f85149)', padding: 8 }}>{error}</div>
      ) : null}
      <div style={{ ...S.body, ...S.split }}>
        <div style={S.list}>
          {runs.length === 0 ? (
            <div style={S.empty}>
              No trajectories yet.
              <br />
              Capture is off by default — turn it on for a dataset in the Datasets section, or push
              runs in with the Python SDK.
            </div>
          ) : null}
          {runs.map((run, index) => (
            <div
              key={run.id}
              onClick={() => void open(run.id)}
              style={{
                padding: '8px 12px',
                borderBottom: '1px solid var(--border, #30363d)',
                borderLeft: `3px solid ${outcomeColor(run.outcome)}`,
                cursor: 'pointer',
                background:
                  selected?.id === run.id ? 'var(--bg-secondary, #161b22)' : 'transparent',
                animation: `traj-in 200ms ease-out ${Math.min(index, 15) * 18}ms both`,
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {run.goal || <em style={{ opacity: 0.6 }}>(no goal)</em>}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 3, ...S.mono }}>
                <span>{run.source}</span>
                <span>{run.steps} steps</span>
                <span>{ms(run.duration_ms)}</span>
                <span style={{ flex: 1 }} />
                <span>{ago(run.started_at)}</span>
              </div>
            </div>
          ))}
        </div>
        <div style={S.detail}>
          {selected ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
                <button style={S.button} onClick={() => void remove(selected.id)}>
                  <TrashIcon /> Delete
                </button>
              </div>
              <RunDetail run={selected} onChanged={() => void open(selected.id)} />
            </>
          ) : (
            <div style={S.empty}>Select a run to walk its steps.</div>
          )}
        </div>
      </div>
    </>
  );
}

// --- Datasets ---------------------------------------------------------------

function DatasetsSection() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [name, setName] = useState('');
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      setDatasets(await listDatasets());
      setStats(await getStats());
      setError('');
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const add = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const id = trimmed
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 60);
    try {
      await createDataset({ id, name: trimmed });
      setName('');
      void refresh();
    } catch (err) {
      setError(String(err));
    }
  };

  const toggleCapture = async (dataset: Dataset) => {
    await updateDataset(dataset.id, { capture: !dataset.capture });
    void refresh();
  };

  return (
    <>
      <div style={S.bar}>
        <span style={S.heading}>Datasets</span>
        <input
          style={{ ...S.input, width: 200 }}
          placeholder="new dataset name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void add()}
        />
        <button style={S.button} onClick={() => void add()}>
          <DatabaseIcon /> Create
        </button>
        <span style={{ flex: 1 }} />
        <button style={S.button} onClick={() => void refresh()}>
          <RefreshIcon /> Refresh
        </button>
      </div>
      {error ? (
        <div style={{ ...S.mono, color: 'var(--danger, #f85149)', padding: 8 }}>{error}</div>
      ) : null}
      <div style={{ ...S.body, padding: 16 }}>
        {stats ? (
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
            {[
              ['Runs', stats.runs],
              ['Success', stats.outcomes.success ?? 0],
              ['Failure', stats.outcomes.failure ?? 0],
              ['Ungraded', stats.outcomes.ungraded ?? 0],
            ].map(([label, value]) => (
              <div key={String(label)} style={{ ...S.card, minWidth: 110 }}>
                <div style={S.heading}>{label}</div>
                <div style={{ fontSize: 24, fontFamily: S.mono.fontFamily }}>
                  <Rolling value={Number(value)} />
                </div>
              </div>
            ))}
          </div>
        ) : null}

        <div style={{ ...S.heading, marginBottom: 8 }}>Collections</div>
        {datasets.length === 0 ? (
          <div style={S.empty}>No datasets. Create one, then switch capture on.</div>
        ) : null}
        {datasets.map((dataset) => (
          <div
            key={dataset.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '8px 0',
              borderBottom: '1px solid var(--border, #30363d)',
            }}
          >
            <span style={{ color: dataset.capture ? 'var(--danger, #f85149)' : 'inherit' }}>
              <RecordIcon />
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600 }}>{dataset.name}</div>
              <div style={S.mono}>
                {dataset.id} · {dataset.run_count} runs · {dataset.source_kind}
              </div>
            </div>
            <button style={S.button} onClick={() => void toggleCapture(dataset)}>
              {dataset.capture ? 'Stop capture' : 'Capture here'}
            </button>
          </div>
        ))}

        <div style={{ ...S.heading, margin: '20px 0 8px' }}>Tool usage</div>
        {stats && stats.tools.length ? (
          stats.tools.map((tool) => (
            <div key={tool.name} style={{ display: 'flex', gap: 12, padding: '4px 0', ...S.mono }}>
              <span style={{ flex: 1, color: 'var(--text-primary, #c9d1d9)' }}>{tool.name}</span>
              <span>{tool.calls} calls</span>
              <span
                style={{
                  color: tool.failures ? 'var(--danger, #f85149)' : 'inherit',
                  minWidth: 70,
                  textAlign: 'right',
                }}
              >
                {tool.failures} failed
              </span>
              <span
                style={{
                  color: tool.gated ? 'var(--warning, #d29922)' : 'inherit',
                  minWidth: 70,
                  textAlign: 'right',
                }}
              >
                {tool.gated} gated
              </span>
              <span style={{ minWidth: 60, textAlign: 'right' }}>{ms(tool.avgMs)}</span>
            </div>
          ))
        ) : (
          <div style={S.empty}>No tool calls recorded yet.</div>
        )}
      </div>
    </>
  );
}

// --- Harness ----------------------------------------------------------------

function HarnessSection() {
  const [harnesses, setHarnesses] = useState<Harness[]>([]);
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [report, setReport] = useState<CompareReport | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    listHarnesses()
      .then(setHarnesses)
      .catch((err) => setError(String(err)));
  }, []);

  const run = async () => {
    if (!a || !b) return;
    try {
      setReport(await compareHarnesses(a, b));
      setError('');
    } catch (err) {
      setError(String(err));
    }
  };

  const options = useMemo(
    () =>
      harnesses.map((h) => (
        <option key={h.fingerprint} value={h.fingerprint}>
          {h.label} · {h.fingerprint} ({h.run_count} runs)
        </option>
      )),
    [harnesses],
  );

  return (
    <>
      <div style={S.bar}>
        <span style={S.heading}>Harness</span>
        <select style={S.input} value={a} onChange={(e) => setA(e.target.value)}>
          <option value="">baseline…</option>
          {options}
        </select>
        <span style={S.mono}>vs</span>
        <select style={S.input} value={b} onChange={(e) => setB(e.target.value)}>
          <option value="">candidate…</option>
          {options}
        </select>
        <button style={S.button} onClick={() => void run()}>
          <ScaleIcon /> Compare
        </button>
      </div>
      {error ? (
        <div style={{ ...S.mono, color: 'var(--danger, #f85149)', padding: 8 }}>{error}</div>
      ) : null}
      <div style={{ ...S.body, padding: 16 }}>
        {!report ? (
          <div style={S.empty}>
            Pick two harnesses to compare.
            <br />A harness is a system prompt, a tool catalog, a model and its sampling settings —
            changing any of them makes a new one.
          </div>
        ) : (
          <>
            <div
              style={{
                ...S.card,
                borderTopColor: report.comparable
                  ? 'var(--success, #3fb950)'
                  : 'var(--warning, #d29922)',
                marginBottom: 16,
              }}
            >
              <div style={S.heading}>
                {report.comparable ? 'Paired comparison' : 'Not a comparison'}
              </div>
              <div style={{ fontSize: 12, marginTop: 4 }}>{report.note}</div>
              {report.comparable ? (
                <div style={{ ...S.mono, marginTop: 6 }}>
                  baseline {report.pairedSuccess.a}/{report.pairedSuccess.of} · candidate{' '}
                  {report.pairedSuccess.b}/{report.pairedSuccess.of}
                </div>
              ) : null}
            </div>

            <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
              {[report.a, report.b].map((side, index) => (
                <div key={side.fingerprint} style={{ ...S.card, flex: 1, minWidth: 220 }}>
                  <div style={S.heading}>{index === 0 ? 'Baseline' : 'Candidate'}</div>
                  <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>{side.label}</div>
                  <div style={{ fontSize: 26, fontFamily: S.mono.fontFamily, marginTop: 6 }}>
                    {side.successRate == null ? (
                      <span style={{ fontSize: 14, opacity: 0.7 }}>ungraded</span>
                    ) : (
                      <Rolling value={side.successRate * 100} suffix="%" />
                    )}
                  </div>
                  <div style={S.mono}>
                    {side.runs} runs · {side.graded} graded · {side.avgSteps} steps avg
                  </div>
                  <div style={S.mono}>{side.fingerprint}</div>
                </div>
              ))}
            </div>

            {report.regressions.length ? (
              <div style={{ marginBottom: 14 }}>
                <div style={{ ...S.heading, color: 'var(--danger, #f85149)' }}>
                  Regressions — baseline did these, candidate does not
                </div>
                {report.regressions.map((goal) => (
                  <div key={goal} style={{ ...S.mono, marginTop: 3 }}>
                    {goal}
                  </div>
                ))}
              </div>
            ) : null}

            {report.fixes.length ? (
              <div style={{ marginBottom: 14 }}>
                <div style={{ ...S.heading, color: 'var(--success, #3fb950)' }}>
                  Fixed — candidate does these, baseline does not
                </div>
                {report.fixes.map((goal) => (
                  <div key={goal} style={{ ...S.mono, marginTop: 3 }}>
                    {goal}
                  </div>
                ))}
              </div>
            ) : null}

            <div style={{ ...S.heading, marginBottom: 6 }}>Tool calls per run</div>
            {report.toolDelta.map((row) => (
              <div key={row.name} style={{ display: 'flex', gap: 12, ...S.mono, padding: '3px 0' }}>
                <span style={{ flex: 1, color: 'var(--text-primary, #c9d1d9)' }}>{row.name}</span>
                <span style={{ minWidth: 60, textAlign: 'right' }}>{row.a}</span>
                <span style={{ minWidth: 60, textAlign: 'right' }}>{row.b}</span>
                <span
                  style={{
                    minWidth: 70,
                    textAlign: 'right',
                    color:
                      row.delta > 0
                        ? 'var(--warning, #d29922)'
                        : row.delta < 0
                          ? 'var(--accent, #58a6ff)'
                          : 'inherit',
                  }}
                >
                  {row.delta > 0 ? '+' : ''}
                  {row.delta}
                </span>
              </div>
            ))}
          </>
        )}
      </div>
    </>
  );
}

// --- the pane ---------------------------------------------------------------

export function TrajectoriesHub() {
  const { section } = usePaneSection();
  return (
    <div style={S.pane}>
      <style>{`@keyframes traj-in {
        from { opacity: 0; transform: translateY(4px); }
        to { opacity: 1; transform: none; }
      }`}</style>
      {section === 'datasets' ? (
        <DatasetsSection />
      ) : section === 'harness' ? (
        <HarnessSection />
      ) : (
        <RunsSection />
      )}
    </div>
  );
}
