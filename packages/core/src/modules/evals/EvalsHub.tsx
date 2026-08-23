import { useCallback, useEffect, useMemo, useState } from 'react';

import { DataList, DataRow, PickRow, RollingNumber } from '../../DataList';
import { dialogs } from '../../dialogs';
import { usePaneSection } from '../../layout/use-sections';
import { subscribeChannel } from '../../ws';
import {
  benchmarkPresets,
  cancelSweep,
  CASE_TYPES,
  comparePreview,
  createSuite,
  emptyBenchmark,
  emptyCase,
  EXPOSE_MODES,
  forkSuite,
  getDiff,
  getLeaderboard,
  getRun,
  GRADES,
  listCases,
  listRuns,
  listSuites,
  listSweeps,
  METRICS,
  peekDataset,
  putCases,
  splitBase,
  startRun,
  suggestTargets,
  type ActiveSweep,
  type BenchmarkPreset,
  type BoardCase,
  type BoardRun,
  type CaseResult,
  type ComparePreview,
  type DatasetPeek,
  type EvalCase,
  type EvalRun,
  type EvalSuite,
  type HfBenchmark,
  type Leaderboard,
  type RunDiff,
  type SuggestedTarget,
  type ToolCall,
} from './api';

/**
 * Evals: one pane, four sections — Suites, Run, Results, Compare.
 *
 * One pane rather than four, following the pane-consolidation rule: these are
 * four views of the same object (a suite, a sweep of it, what the sweep found,
 * and how its runs compare) and splitting them would mean four openers and four
 * copies of the suite selection. The section is which of the four you see.
 *
 * The Results section is the point of the module. A pass rate you cannot explain
 * is not actionable, so every failing row carries the detail line, what was
 * expected, and what the model actually called — and the case list is failure-first
 * because nobody opens this pane to read the passes.
 */

const S = {
  pane: {
    display: 'flex',
    flexDirection: 'column' as const,
    height: '100%',
    overflow: 'hidden',
    background: 'var(--bg, #14161a)',
    color: 'var(--text, #d7dae0)',
    fontSize: 13,
  },
  bar: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 12px',
    borderBottom: '1px solid var(--border, #2e333d)',
    flexShrink: 0,
  },
  // Headings: uppercase with heavy tracking; metadata and numbers monospace and
  // muted. The two never share a treatment, so a row scans as label-then-figures.
  heading: {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.12em',
    textTransform: 'uppercase' as const,
    color: 'var(--text-dim, #8a909c)',
  },
  mono: {
    fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)',
    fontSize: 11,
    color: 'var(--text-dim, #8a909c)',
  },
  scroll: { flex: 1, overflowY: 'auto' as const, padding: 12 },
  input: {
    background: 'var(--bg, #14161a)',
    border: '1px solid var(--border, #2e333d)',
    color: 'var(--text, #d7dae0)',
    borderRadius: 4,
    padding: '4px 8px',
    fontSize: 12,
    width: '100%',
  },
  // A read-only suite is marked, not hidden: it is the worked example, and the
  // badge is what explains why editing it is refused.
  badge: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.08em',
    textTransform: 'uppercase' as const,
    color: 'var(--text-dim, #8a909c)',
    border: '1px solid var(--border, #2e333d)',
    borderRadius: 3,
    padding: '1px 5px',
  },
  field: { marginBottom: 8 },
  // Section heads carry their own count on the right. A heading that has to
  // sit above a list of unknown length is a heading that tells you nothing.
  sectionHead: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 8,
    marginBottom: 6,
  },
  label: {
    display: 'block',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.1em',
    textTransform: 'uppercase' as const,
    color: 'var(--text-dim, #8a909c)',
    marginBottom: 3,
  },
  button: {
    background: 'var(--bg-raised, #1d2026)',
    border: '1px solid var(--border, #2e333d)',
    color: 'var(--text, #d7dae0)',
    borderRadius: 4,
    padding: '4px 10px',
    fontSize: 12,
    cursor: 'pointer',
  },
  select: {
    background: 'var(--bg-raised, #1d2026)',
    border: '1px solid var(--border, #2e333d)',
    color: 'var(--text, #d7dae0)',
    borderRadius: 4,
    padding: '4px 8px',
    fontSize: 12,
  },
  // A left border accent rather than a full glowing perimeter: the colour marks
  // the verdict without turning every row into a card.
  row: (passed: boolean) => ({
    borderLeft: `2px solid ${passed ? 'var(--success, #3fb950)' : 'var(--danger, #e06c75)'}`,
    background: 'var(--bg-raised, #1d2026)',
    padding: '8px 10px',
    marginBottom: 6,
  }),
};

function pct(run: EvalRun): string {
  if (!run.completed) return '—';
  return `${Math.round((run.passed / run.completed) * 100)}%`;
}

/**
 * A run's verdict.
 *
 * Three states, not two: a run still going is not a failure, and drawing it as
 * one is how a sweep at 3/50 comes to look broken thirty seconds after it was
 * started. Only a finished run with a shortfall is a failure.
 */
function runKind(run: EvalRun): 'ok' | 'fail' | 'info' {
  if (run.error) return 'fail';
  if (run.status !== 'done') return 'info';
  return run.passed === run.total ? 'ok' : 'fail';
}

function describeCalls(calls: ToolCall[]): string {
  if (!calls.length) return 'none';
  return calls.map((c) => c.name).join(' → ');
}

/**
 * Write one case by hand.
 *
 * The pane's half of authoring; the agent's half is `evals.addCase`. Both write
 * the same `.jsonl`, and neither is the only way in — the file is a buffer you can
 * open in the editor, which is what keeps a suite reviewable and diffable.
 *
 * The form is deliberately shallow: id, prompt, grade, exposure, expected calls.
 * Anything richer (fixtures, history, workspace context) is a JSON field, because
 * building a form for arbitrary tool arguments would be building a worse editor
 * than the one already in the app.
 */
/**
 * The benchmark half of the case editor.
 *
 * Its job is not to collect eleven fields — it is to stop you authoring the case
 * that scores zero. Every benchmark this module has got wrong was wrong the same
 * way: the template named a column the dataset does not have, or the target column
 * held the worked solution rather than the answer. Both look exactly like a bad
 * model, and both are invisible until ten minutes of run time have been spent.
 *
 * So the form is built around **peeking at the real dataset**: `first-rows` gives
 * the columns and a few actual rows without downloading anything, the column
 * pickers are populated from that, and the comparison preview runs the *same*
 * extraction the harness runs over a real row. A regex that will fail at run time
 * fails here, in front of you, before the run.
 */
function BenchmarkFields({
  bench,
  onChange,
}: {
  bench: HfBenchmark;
  onChange: (b: HfBenchmark) => void;
}) {
  const [peek, setPeek] = useState<DatasetPeek | null>(null);
  const [peeking, setPeeking] = useState(false);
  const [peekError, setPeekError] = useState('');
  const [presets, setPresets] = useState<BenchmarkPreset[]>([]);
  const [sampleReply, setSampleReply] = useState('The answer is 18.');
  const [preview, setPreview] = useState<ComparePreview | null>(null);

  const set = (patch: Partial<HfBenchmark>) => onChange({ ...bench, ...patch });

  useEffect(() => {
    benchmarkPresets()
      .then(setPresets)
      .catch(() => setPresets([]));
  }, []);

  const doPeek = useCallback(() => {
    if (!bench.dataset.trim()) return setPeekError('name a dataset first');
    setPeeking(true);
    setPeekError('');
    peekDataset({
      dataset: bench.dataset.trim(),
      config: bench.config,
      split: splitBase(bench.split) || 'train',
      limit: 3,
    })
      .then((p) => {
        setPeek(p);
        // The Hub resolves the config when it was blank; adopting the answer is
        // the point of asking — `gsm8k` has no default one and omitting it fails
        // in a way that reads as "the dataset is broken".
        if (!bench.config && p.config) set({ config: p.config });
      })
      .catch((e) => {
        setPeek(null);
        // Degraded, not blocked: the fields stay editable so a gated or private
        // dataset can still be authored by hand.
        setPeekError(String((e as Error)?.message || e));
      })
      .finally(() => setPeeking(false));
  }, [bench.dataset, bench.config, bench.split]);

  // The comparison preview re-runs whenever anything it depends on moves. It is a
  // backend call because it uses the harness's own extract/normalise — a
  // reimplementation in TypeScript could disagree with the thing that produces the
  // real score, which is the one disagreement this module cannot afford.
  useEffect(() => {
    const row = peek?.rows?.[0];
    if (!row) return setPreview(null);
    let live = true;
    comparePreview({
      row,
      input_template: bench.input_template,
      target_column: bench.target_column,
      target_regex: bench.target_regex,
      prediction_regex: bench.prediction_regex,
      sample_prediction: sampleReply,
    })
      .then((r) => live && setPreview(r))
      .catch(() => live && setPreview(null));
    return () => {
      live = false;
    };
  }, [
    peek,
    bench.input_template,
    bench.target_column,
    bench.target_regex,
    bench.prediction_regex,
    sampleReply,
  ]);

  const columns = peek?.columns ?? [];

  return (
    <>
      {presets.length > 0 && (
        <div style={S.field}>
          <label style={S.label}>Start from a known dataset</label>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {presets.map((p) => (
              <button
                key={p.id}
                style={S.button}
                title={p.why}
                onClick={() => {
                  onChange({ ...bench, ...p.benchmark } as HfBenchmark);
                  setPeek(null);
                  setPreview(null);
                }}
              >
                {p.label.split(' — ')[0]}
              </button>
            ))}
          </div>
          <div style={{ ...S.mono, marginTop: 4 }}>
            Presets carry the regexes these datasets need. GSM8K without them scores zero against a
            model answering perfectly.
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        <div style={{ ...S.field, flex: 2 }}>
          <label style={S.label}>Hub dataset</label>
          <input
            style={S.input}
            value={bench.dataset}
            placeholder="openai/gsm8k"
            onChange={(e) => set({ dataset: e.target.value })}
          />
        </div>
        <div style={{ ...S.field, flex: 1 }}>
          <label style={S.label}>Config</label>
          <input
            style={S.input}
            value={bench.config}
            placeholder="main"
            onChange={(e) => set({ config: e.target.value })}
          />
        </div>
        <div style={{ ...S.field, flex: 1 }}>
          <label style={S.label}>Split</label>
          <input
            style={S.input}
            value={bench.split}
            placeholder="test[:50]"
            onChange={(e) => set({ split: e.target.value })}
          />
        </div>
        <div style={{ ...S.field, display: 'flex', alignItems: 'flex-end' }}>
          <button style={S.button} onClick={doPeek} disabled={peeking}>
            {peeking ? 'Peeking…' : 'Peek'}
          </button>
        </div>
      </div>

      {peekError && (
        <div style={{ ...S.mono, color: 'var(--warn, #e2c08d)' }}>
          {peekError} — the fields still work, you just have to know the columns.
        </div>
      )}
      {peek && (
        <div style={{ ...S.mono, marginBottom: 8 }}>
          {peek.config}/{peek.split} · columns: {peek.columns.join(', ')}
        </div>
      )}

      <div style={S.field}>
        <label style={S.label}>Prompt template — {'{column}'} is filled from the row</label>
        <input
          style={S.input}
          value={bench.input_template}
          placeholder="{question}"
          onChange={(e) => set({ input_template: e.target.value })}
        />
        {columns.length > 0 && (
          <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
            {columns.map((c) => (
              <button
                key={c}
                style={{ ...S.button, fontSize: 11, padding: '1px 6px' }}
                onClick={() => set({ input_template: `${bench.input_template}{${c}}` })}
              >
                +{'{'}
                {c}
                {'}'}
              </button>
            ))}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        <div style={{ ...S.field, flex: 1 }}>
          <label style={S.label}>Answer column</label>
          {columns.length > 0 ? (
            <select
              style={{ ...S.select, width: '100%' }}
              value={bench.target_column}
              onChange={(e) => set({ target_column: e.target.value })}
            >
              {!columns.includes(bench.target_column) && (
                <option value={bench.target_column}>{bench.target_column}</option>
              )}
              {columns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          ) : (
            <input
              style={S.input}
              value={bench.target_column}
              onChange={(e) => set({ target_column: e.target.value })}
            />
          )}
        </div>
        <div style={{ ...S.field, flex: 1 }}>
          <label style={S.label}>Metric</label>
          <select
            style={{ ...S.select, width: '100%' }}
            value={bench.metric}
            onChange={(e) => set({ metric: e.target.value })}
          >
            {METRICS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div style={{ ...S.field, flex: 1 }}>
          <label style={S.label}>Rows</label>
          <input
            style={S.input}
            type="number"
            value={bench.limit}
            onChange={(e) => set({ limit: Number(e.target.value) || 0 })}
          />
        </div>
        <div style={{ ...S.field, flex: 1 }}>
          <label style={S.label}>Pass mark</label>
          <input
            style={S.input}
            type="number"
            step="0.05"
            value={bench.threshold}
            onChange={(e) => set({ threshold: Number(e.target.value) || 0 })}
          />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        <div style={{ ...S.field, flex: 1 }}>
          <label style={S.label}>Answer regex — extract from the reference</label>
          <input
            style={S.input}
            value={bench.target_regex}
            placeholder="####\s*(.+)"
            onChange={(e) => set({ target_regex: e.target.value })}
          />
        </div>
        <div style={{ ...S.field, flex: 1 }}>
          <label style={S.label}>Reply regex — extract from the model</label>
          <input
            style={S.input}
            value={bench.prediction_regex}
            placeholder="(-?[\d.,]+)[^\d]*$"
            onChange={(e) => set({ prediction_regex: e.target.value })}
          />
        </div>
      </div>

      <div style={S.field}>
        <label style={S.label}>System prompt for every row</label>
        <input
          style={S.input}
          value={bench.system}
          placeholder="Solve the problem. End your reply with just the final number."
          onChange={(e) => set({ system: e.target.value })}
        />
      </div>

      {/* The point of the whole form: what would actually be compared, on a real
          row, using the harness's own extraction. */}
      {peek && (
        <div
          style={{
            border: '1px solid var(--border, #2e333d)',
            borderTop: '2px solid var(--accent, #58a6ff)',
            padding: '8px 10px',
            marginBottom: 8,
          }}
        >
          <div style={{ ...S.heading, marginBottom: 6 }}>What will be compared</div>
          <div style={S.field}>
            <label style={S.label}>A reply to test the regex against</label>
            <input
              style={S.input}
              value={sampleReply}
              onChange={(e) => setSampleReply(e.target.value)}
            />
          </div>
          {preview ? (
            <>
              {preview.problems.map((p) => (
                <div
                  key={p}
                  style={{
                    ...S.mono,
                    color: 'var(--warn, #e2c08d)',
                    marginBottom: 4,
                  }}
                >
                  ⚠ {p}
                </div>
              ))}
              <table style={{ width: '100%', fontSize: 11 }}>
                <tbody>
                  <Cmp label="prompt" value={preview.prompt} />
                  <Cmp label="reference (raw)" value={preview.reference_raw} dim />
                  <Cmp label="reference → compared" value={preview.reference_normalised} />
                  <Cmp label="reply → compared" value={preview.prediction_normalised} />
                </tbody>
              </table>
              <div style={{ ...S.mono, marginTop: 6 }}>{verdict(preview, bench.metric)}</div>
            </>
          ) : (
            <div style={S.mono}>…</div>
          )}
        </div>
      )}
    </>
  );
}

/** One row of the comparison table, clipped — a dataset row can be a page long. */
function Cmp({ label, value, dim }: { label: string; value: string; dim?: boolean }) {
  const text = (value || '').replace(/\s+/g, ' ').trim();
  return (
    <tr>
      <td style={{ ...S.label, width: 150, verticalAlign: 'top', paddingRight: 8 }}>{label}</td>
      <td
        style={{
          ...S.mono,
          color: dim ? 'var(--text-dim, #8a909c)' : 'var(--text, #d7dae0)',
          wordBreak: 'break-word',
        }}
      >
        {text.length > 220 ? `${text.slice(0, 220)}…` : text || '—'}
      </td>
    </tr>
  );
}

/** Would this row score? Computed from the same normalised strings the table
 * shows, so the verdict and the evidence for it cannot disagree. */
function verdict(preview: ComparePreview, metric: string): string {
  const ref = preview.reference_normalised;
  const got = preview.prediction_normalised;
  if (!ref || !got) return 'fill in a sample reply to see whether this row would score';
  const hit = metric === 'exact_match' ? ref === got : got.includes(ref);
  return hit
    ? `✓ this row would score 1 under ${metric}`
    : `✗ this row would score 0 under ${metric} — ${JSON.stringify(got)} vs ${JSON.stringify(ref)}`;
}

/**
 * Compare: the ranking, the case matrix, and what nothing passes.
 *
 * A per-run pass rate is a number you cannot act on, so this answers three
 * different questions instead of one. Who is ahead (cheap, least interesting);
 * what a change fixed and broke, over the cases both runs actually attempted; and
 * which cases *everything* fails — the last because three separate times a case in
 * this module was wrong rather than the model, and a universal failure is a prompt
 * to go and read the case.
 */
function Compare({ selected }: { selected: string }) {
  const [board, setBoard] = useState<Leaderboard | null>(null);
  const [error, setError] = useState('');
  const [base, setBase] = useState('');
  const [other, setOther] = useState('');
  const [diff, setDiff] = useState<RunDiff | null>(null);

  useEffect(() => {
    if (!selected) return setBoard(null);
    setError('');
    getLeaderboard(selected)
      .then((b) => {
        setBoard(b);
        // Default the comparison to oldest → newest, which is the one people
        // nearly always want: what has changed since.
        const runs = b.runs;
        setBase(runs.length > 1 ? runs[runs.length - 1].id : '');
        setOther(runs.length > 1 ? runs[0].id : '');
      })
      .catch((e) => {
        setBoard(null);
        setError(String((e as Error)?.message || e));
      });
  }, [selected]);

  useEffect(() => {
    if (!base || !other || base === other) return setDiff(null);
    let live = true;
    getDiff(base, other)
      .then((d) => live && setDiff(d))
      .catch(() => live && setDiff(null));
    return () => {
      live = false;
    };
  }, [base, other]);

  if (!selected) return <div style={S.scroll}>Pick a suite to compare its runs.</div>;
  if (error) return <div style={{ ...S.scroll, ...S.mono }}>{error}</div>;
  if (!board) return <div style={S.scroll}>…</div>;
  if (board.runs.length === 0) {
    return (
      <div style={S.scroll}>
        No finished runs for this suite yet. A sweep in progress is deliberately not shown — it
        would look like a model failing everything it has not reached.
      </div>
    );
  }

  return (
    <div style={S.scroll}>
      <div style={{ ...S.heading, marginBottom: 8 }}>Ranking</div>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 16 }}>
        <tbody>
          {[...board.runs]
            .sort((a, b) => b.rate - a.rate)
            .map((r, i) => (
              <RankRow key={r.id} run={r} place={i + 1} />
            ))}
        </tbody>
      </table>

      {board.universalFailures.length > 0 && (
        <div
          style={{
            border: '1px solid var(--border, #2e333d)',
            borderLeft: '2px solid var(--warn, #e2c08d)',
            padding: '8px 10px',
            marginBottom: 16,
          }}
        >
          <div style={{ ...S.heading, marginBottom: 4 }}>
            Failed by every run — suspect the case
          </div>
          <div style={{ ...S.mono, marginBottom: 6 }}>
            A case nothing passes is usually a case that is wrong, not a capability every model
            lacks. Read the expectation against the tool&rsquo;s own description before treating
            this as a model problem.
          </div>
          {board.universalFailures.map((id) => (
            <div key={id} style={{ ...S.mono, color: 'var(--warn, #e2c08d)' }}>
              {id}
            </div>
          ))}
        </div>
      )}

      {board.runs.length > 1 && (
        <>
          <div style={{ ...S.heading, marginBottom: 8 }}>What changed</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <select style={S.select} value={base} onChange={(e) => setBase(e.target.value)}>
              {board.runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
            <span style={S.mono}>→</span>
            <select style={S.select} value={other} onChange={(e) => setOther(e.target.value)}>
              {board.runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
          </div>
          {diff && <Diff diff={diff} />}
        </>
      )}

      <div style={{ ...S.heading, margin: '16px 0 8px' }}>Case matrix</div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 11, minWidth: '100%' }}>
          <thead>
            <tr>
              <th style={{ ...S.label, textAlign: 'left', padding: '4px 8px 4px 0' }}>Case</th>
              {board.runs.map((r) => (
                <th
                  key={r.id}
                  style={{ ...S.label, padding: '4px 6px', whiteSpace: 'nowrap' }}
                  title={`${r.model} · ${r.startedAt}`}
                >
                  {r.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {board.cases.map((c) => (
              <MatrixRow key={c.caseId} row={c} runs={board.runs} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RankRow({ run, place }: { run: BoardRun; place: number }) {
  return (
    <tr>
      <td style={{ ...S.mono, padding: '3px 8px 3px 0', width: 24 }}>{place}</td>
      <td style={{ padding: '3px 8px 3px 0' }}>{run.label}</td>
      <td style={{ ...S.mono, padding: '3px 8px', whiteSpace: 'nowrap' }}>
        {run.passed}/{run.attempted}
      </td>
      <td style={{ ...S.mono, padding: '3px 8px', width: 56, textAlign: 'right' }}>
        {(run.rate * 100).toFixed(0)}%
      </td>
      <td style={{ padding: '3px 8px', width: '40%' }}>
        {/* A bar rather than a number alone: the gap between 42% and 67% is the
            thing being read, and a column of percentages does not show it. */}
        <div style={{ background: 'var(--bg, #14161a)', height: 6 }}>
          <div
            style={{
              width: `${Math.round(run.rate * 100)}%`,
              height: '100%',
              background: 'var(--success, #3fb950)',
            }}
          />
        </div>
      </td>
      <td style={{ ...S.mono, padding: '3px 0 3px 8px', whiteSpace: 'nowrap' }}>
        {run.avgRounds.toFixed(1)} rounds
        {run.errored > 0 ? ` · ${run.errored} errored` : ''}
      </td>
    </tr>
  );
}

function Diff({ diff }: { diff: RunDiff }) {
  const dropped = diff.onlyInBase.length + diff.onlyInOther.length;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ ...S.mono, marginBottom: 6 }}>
        {diff.shared} case{diff.shared === 1 ? '' : 's'} in common
        {dropped > 0 && ` · ${dropped} attempted by only one of them, and left out`}
      </div>
      {diff.hashesUnknown && (
        <div style={{ ...S.mono, color: 'var(--warn, #e2c08d)', marginBottom: 6 }}>
          ⚠ these runs predate case hashing, so a case that was edited between them cannot be told
          apart from one the model got better at.
        </div>
      )}
      <HarnessBanner harness={diff.harness} />
      <DiffGroup label="Fixed" tone="var(--success, #3fb950)" items={diff.fixed} />
      <DiffGroup label="Broke" tone="var(--danger, #e06c75)" items={diff.broken} />
      <DiffGroup
        label="Case edited — not a fix or a regression"
        tone="var(--warn, #e2c08d)"
        items={diff.changed}
      />
      <DiffGroup
        label="Errored — something other than the model broke"
        tone="var(--warn, #e2c08d)"
        items={diff.errored}
      />
      {diff.fixed.length === 0 &&
        diff.broken.length === 0 &&
        diff.changed.length === 0 &&
        diff.errored.length === 0 && (
          <div style={S.mono}>No case changed verdict between these two runs.</div>
        )}
    </div>
  );
}

/**
 * Whether the two runs were even measured against the same tool catalog.
 *
 * Above the fixed/broke lists rather than inside them, because it invalidates the
 * whole comparison rather than any one row: enabled skills ride every turn and each
 * connected MCP server contributes a tool group, so toggling either between runs
 * moves the pass rate for reasons that have nothing to do with the model. The
 * unknown case is drawn distinctly from the differing one — a run with no recorded
 * harness is not a run whose harness matched.
 */
function HarnessBanner({ harness }: { harness: RunDiff['harness'] }) {
  if (harness.unknown) {
    return (
      <div style={{ ...S.mono, color: 'var(--text-dim, #8b949e)', marginBottom: 6 }}>
        The tool catalog was not recorded for one of these runs, so a skill or MCP server
        toggled between them cannot be ruled out.
      </div>
    );
  }
  if (!harness.differs) return null;
  return (
    <div
      style={{
        ...S.mono,
        color: 'var(--warn, #e2c08d)',
        borderLeft: '2px solid var(--warn, #e2c08d)',
        paddingLeft: 8,
        marginBottom: 8,
      }}
    >
      <div>
        ⚠ these two runs saw <strong>different tool catalogs</strong> — what changed below may be
        the harness, not the model.
      </div>
      {harness.changes.map((line) => (
        <div key={line} style={{ opacity: 0.85 }}>
          · {line}
        </div>
      ))}
    </div>
  );
}

function DiffGroup({
  label,
  tone,
  items,
}: {
  label: string;
  tone: string;
  items: { caseId: string; detail: string }[];
}) {
  if (items.length === 0) return null;
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ ...S.label, color: tone }}>
        {label} ({items.length})
      </div>
      {items.map((i) => (
        <div key={i.caseId} style={{ ...S.mono, paddingLeft: 8 }}>
          {i.caseId}
          {i.detail ? ` — ${i.detail}` : ''}
        </div>
      ))}
    </div>
  );
}

function MatrixRow({ row, runs }: { row: BoardCase; runs: BoardRun[] }) {
  return (
    <tr>
      <td
        style={{
          ...S.mono,
          padding: '3px 8px 3px 0',
          color: row.universalFailure ? 'var(--warn, #e2c08d)' : 'var(--text, #d7dae0)',
          whiteSpace: 'nowrap',
        }}
        title={row.edited ? 'this case was edited between these runs' : undefined}
      >
        {row.caseId}
        {row.edited && ' ✎'}
      </td>
      {runs.map((r) => {
        const verdict = row.verdicts[r.id];
        return (
          <td
            key={r.id}
            style={{ textAlign: 'center', padding: '3px 6px' }}
            title={row.details[r.id] || ''}
          >
            {/* Three states, not two: a run that did not attempt a case has not
                failed it, and rendering both as a cross would invent regressions. */}
            {verdict === undefined ? (
              <span style={{ color: 'var(--text-dim, #8a909c)' }}>·</span>
            ) : verdict ? (
              <span style={{ color: 'var(--success, #3fb950)' }}>✓</span>
            ) : (
              <span style={{ color: 'var(--danger, #e06c75)' }}>✕</span>
            )}
          </td>
        );
      })}
    </tr>
  );
}

function CaseEditor({
  suite,
  initial,
  onSave,
  onCancel,
}: {
  suite: EvalSuite;
  initial: EvalCase;
  onSave: (c: EvalCase) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<EvalCase>(initial);
  const [callsText, setCallsText] = useState(() => JSON.stringify(initial.expect.calls, null, 0));
  const [error, setError] = useState('');

  const set = (patch: Partial<EvalCase>) => setDraft({ ...draft, ...patch });
  const noCall = draft.expect.grade === 'no_call';
  const isBench = draft.type === 'hf_benchmark';
  // Kept even while the case is a tool_call, so flipping the type back and forth
  // does not throw away what you typed.
  const [bench, setBench] = useState<HfBenchmark>(() => initial.benchmark ?? emptyBenchmark());

  return (
    <div style={{ ...S.row(true), borderLeftColor: 'var(--accent, #58a6ff)' }}>
      <div style={S.field}>
        <label style={S.label}>Case id</label>
        <input
          style={S.input}
          value={draft.id}
          placeholder="layout-open-terminal"
          onChange={(e) => set({ id: e.target.value })}
        />
      </div>
      <div style={S.field}>
        <label style={S.label}>What this case measures</label>
        <select
          style={{ ...S.select, width: '100%' }}
          value={draft.type}
          onChange={(e) => set({ type: e.target.value })}
        >
          {CASE_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>
      {/* A benchmark's prompt comes from the dataset row, so the field would be
          ignored — it goes away rather than sitting there inviting input, the same
          rule the no_call grade follows below. */}
      {!isBench && (
        <div style={S.field}>
          <label style={S.label}>Prompt</label>
          <input
            style={S.input}
            value={draft.prompt}
            placeholder="open a terminal"
            onChange={(e) => set({ prompt: e.target.value })}
          />
        </div>
      )}
      {isBench && <BenchmarkFields bench={bench} onChange={setBench} />}
      {!isBench && (
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ ...S.field, flex: 1 }}>
            <label style={S.label}>Grade</label>
            <select
              style={{ ...S.select, width: '100%' }}
              value={draft.expect.grade}
              onChange={(e) => set({ expect: { ...draft.expect, grade: e.target.value } })}
            >
              {GRADES.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
          </div>
          <div style={{ ...S.field, flex: 1 }}>
            <label style={S.label}>Exposure</label>
            <select
              style={{ ...S.select, width: '100%' }}
              value={draft.expose.mode}
              onChange={(e) => set({ expose: { ...draft.expose, mode: e.target.value } })}
            >
              {EXPOSE_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <div style={{ ...S.field, flex: 1 }}>
            <label style={S.label}>Preload groups</label>
            <input
              style={S.input}
              value={draft.expose.preload.join(', ')}
              placeholder="layout, github"
              onChange={(e) =>
                set({
                  expose: {
                    ...draft.expose,
                    preload: e.target.value
                      .split(',')
                      .map((g) => g.trim())
                      .filter(Boolean),
                  },
                })
              }
            />
          </div>
        </div>
      )}
      {/* A no_call case has nothing to expect, so the field goes away rather than
          sitting there inviting you to fill in something that would be ignored. */}
      {!isBench && !noCall && (
        <div style={S.field}>
          <label style={S.label}>Expected calls (JSON)</label>
          <input
            style={S.input}
            value={callsText}
            placeholder='[{"name": "show", "arguments": {"target": "terminal"}}]'
            onChange={(e) => setCallsText(e.target.value)}
          />
        </div>
      )}
      <div style={S.field}>
        <label style={S.label}>Note — why this case exists</label>
        <input style={S.input} value={draft.note} onChange={(e) => set({ note: e.target.value })} />
      </div>
      {error && <div style={{ ...S.mono, color: 'var(--danger, #e06c75)' }}>{error}</div>}
      <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
        <button
          style={S.button}
          onClick={() => {
            if (!draft.id.trim()) return setError('a case needs an id');
            if (isBench) {
              if (!bench.dataset.trim()) return setError('a benchmark needs a dataset');
              if (!bench.target_column.trim()) {
                return setError('a benchmark needs an answer column');
              }
              if (!bench.input_template.includes('{')) {
                // Every row would get the identical prompt — a run that takes ten
                // minutes to tell you nothing.
                return setError(
                  'the prompt template has no {column} in it, so every row would ask the same question',
                );
              }
              setError('');
              // The prompt is unused for a benchmark, but the model requires a
              // string; the dataset id is the most useful thing to put there when
              // the case shows up in a list.
              return onSave({
                ...draft,
                prompt: draft.prompt || bench.dataset,
                benchmark: bench,
                expect: { ...draft.expect, calls: [] },
              });
            }
            if (!draft.prompt.trim()) return setError('a case needs a prompt');
            let calls: ToolCall[] = [];
            if (!noCall) {
              try {
                calls = JSON.parse(callsText || '[]');
              } catch (e) {
                return setError(`expected calls: ${String(e)}`);
              }
              if (!Array.isArray(calls) || calls.some((c) => !c?.name)) {
                return setError('expected calls must be a list of {name, arguments}');
              }
              if (calls.length === 0) {
                // The backend refuses this too, but saying it here saves a round
                // trip and explains which grade actually means "expect nothing".
                return setError('no expected calls — use the no_call grade for that');
              }
            }
            setError('');
            onSave({
              ...draft,
              benchmark: null,
              expect: { ...draft.expect, calls: noCall ? [] : calls },
            });
          }}
        >
          Save case
        </button>
        <button style={S.button} onClick={onCancel}>
          Cancel
        </button>
        <span style={{ flex: 1 }} />
        <span style={S.mono}>{suite.name}</span>
      </div>
    </div>
  );
}

/** Suites: what exists, and how to make one. */
function Suites({
  suites,
  selected,
  onSelect,
  onReload,
}: {
  suites: EvalSuite[];
  selected: string;
  onSelect: (id: string) => void;
  onReload: () => void;
}) {
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<EvalCase | null>(null);

  const suite = suites.find((s) => s.id === selected);

  const reloadCases = useCallback(() => {
    if (!selected) return;
    listCases(selected)
      .then((r) => {
        setCases(r.cases);
        setError(r.error);
      })
      .catch((e) => setError(String(e)));
  }, [selected]);

  useEffect(reloadCases, [reloadCases]);

  const save = (edited: EvalCase) => {
    if (!suite) return;
    // Replace by id if it exists, otherwise append. The backend refuses a
    // duplicate id in the file, so "edit" and "add" have to resolve to the same
    // list here rather than both appending.
    const next = cases.some((c) => c.id === edited.id)
      ? cases.map((c) => (c.id === edited.id ? edited : c))
      : [...cases, edited];
    putCases(suite.id, next)
      .then(() => {
        setEditing(null);
        reloadCases();
        onReload();
      })
      .catch((e) => setError(String(e)));
  };

  const remove = (caseId: string) => {
    if (!suite) return;
    putCases(
      suite.id,
      cases.filter((c) => c.id !== caseId),
    )
      .then(() => {
        reloadCases();
        onReload();
      })
      .catch((e) => setError(String(e)));
  };

  return (
    <>
      <div style={S.bar}>
        <span style={S.heading}>Suites</span>
        <select style={S.select} value={selected} onChange={(e) => onSelect(e.target.value)}>
          <option value="">Select a suite…</option>
          {suites.map((s) => (
            <option key={s.id} value={s.id}>
              {s.source === 'bundled' ? '◆ ' : ''}
              {s.name} ({s.case_count})
            </option>
          ))}
        </select>
        <button
          style={S.button}
          onClick={async () => {
            // `dialogs.prompt`, not `window.prompt`: the native one is unthemed,
            // is blocked outright in the Tauri shell, and blocks the whole
            // renderer while it is up.
            const name = await dialogs.prompt({
              title: 'New suite',
              placeholder: 'Suite name',
              confirmLabel: 'Create',
            });
            if (name) createSuite(name).then(onReload);
          }}
        >
          New suite
        </button>
        {/* Fork is the way a bundled suite becomes editable, so it sits next to the
            read-only badge rather than in a menu. */}
        {suite?.read_only && (
          <button
            style={S.button}
            onClick={() =>
              forkSuite(suite.id, `${suite.name} (copy)`).then((f) => {
                onReload();
                onSelect(f.id);
              })
            }
          >
            Fork to edit
          </button>
        )}
        {suite && !suite.read_only && (
          <button style={S.button} onClick={() => setEditing(emptyCase())}>
            Add case
          </button>
        )}
        <span style={{ flex: 1 }} />
        {suite?.read_only && <span style={S.badge}>bundled · read-only</span>}
        {suite && <span style={S.mono}>{suite.path}</span>}
      </div>
      <div style={S.scroll}>
        {/* A parse error is shown *instead of* the cases, with its line number.
            "your JSON is broken on line 12" and "this suite is empty" must never
            look the same. */}
        {error && (
          <div style={{ ...S.row(false), fontFamily: 'inherit' }}>
            <div style={{ fontWeight: 600 }}>This suite could not be parsed</div>
            <div style={S.mono}>{error}</div>
          </div>
        )}
        {editing && suite && (
          <CaseEditor
            suite={suite}
            initial={editing}
            onSave={save}
            onCancel={() => setEditing(null)}
          />
        )}
        {!error && !cases.length && selected && !editing && (
          <div style={S.mono}>
            No cases yet. Add one above, ask the agent to draft some, or open the .jsonl in the
            editor.
          </div>
        )}
        <DataList label="Cases in this suite">
          {cases.map((c, i) => (
            <DataRow
              key={c.id}
              index={i}
              title={c.id}
              // A case has no verdict — it has not been run. `info` marks it as a
              // record rather than claiming it passed, which a tick would.
              kind="info"
              hideMark
              meta={[
                c.expect.grade,
                c.expose.mode,
                ...(c.expose.preload.length ? [c.expose.preload.join(',')] : []),
              ]}
              badge={c.expect.calls.length > 0 ? `${c.expect.calls.length} calls` : undefined}
              actions={
                suite && !suite.read_only ? (
                  <>
                    <button style={S.button} onClick={() => setEditing(c)}>
                      Edit
                    </button>
                    <button style={S.button} onClick={() => remove(c.id)}>
                      Delete
                    </button>
                  </>
                ) : undefined
              }
              footnotes={
                <>
                  {c.expect.calls.length > 0 && (
                    <div style={S.mono}>expects {describeCalls(c.expect.calls)}</div>
                  )}
                  {c.note && <div style={S.mono}>{c.note}</div>}
                </>
              }
            >
              {c.prompt}
            </DataRow>
          ))}
        </DataList>
      </div>
    </>
  );
}

/**
 * A target's identity for selection. The GGUF path when there is one, because two
 * files can share a name across the managed directory and a configured extra dir —
 * keying on the label would make them one checkbox that sweeps whichever the
 * backend listed first.
 */
const targetId = (t: SuggestedTarget): string => t.modelPath || t.label;

/**
 * The localtrack project a suite's sweeps report into.
 *
 * Derived from the suite rather than fixed, and slugged because localtrack uses the
 * string as both the project id and its display name. Falls back to the bare
 * `evals` bucket only when no suite resolved, which is the one case where there is
 * nothing to derive from.
 */
function localtrackProject(suite: EvalSuite | undefined): string {
  if (!suite) return 'evals';
  const slug = suite.name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  return slug ? `evals-${slug}` : `evals-${suite.id}`;
}

/** Run: pick models, start a sweep, watch it. */
function Run({ suites, selected }: { suites: EvalSuite[]; selected: string }) {
  const [targets, setTargets] = useState<SuggestedTarget[]>([]);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState('');
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [sweeps, setSweeps] = useState<ActiveSweep[]>([]);

  useEffect(() => {
    suggestTargets()
      .then(setTargets)
      .catch(() => setTargets([]));
  }, []);

  const reload = useCallback(() => {
    listRuns(selected)
      .then(setRuns)
      .catch(() => setRuns([]));
    // Asked of the node, not derived from `runs`: a sweep started in another
    // window — or before this pane was opened — is still yours to stop, and a
    // `running` row tells you a sweep existed, not that it still does.
    listSweeps()
      .then(setSweeps)
      .catch(() => setSweeps([]));
  }, [selected]);

  useEffect(reload, [reload]);

  // Progress is broadcast, not replied: a sweep outlives the request that started
  // it, so the pane subscribes rather than holding anything open.
  useEffect(() => subscribeChannel('evals', () => reload()), [reload]);

  const suite = suites.find((s) => s.id === selected);

  return (
    <>
      <div style={S.bar}>
        <span style={S.heading}>Run</span>
        <span style={S.mono}>
          {suite ? `${suite.name} · ${suite.case_count} cases` : 'no suite selected'}
        </span>
        <span style={{ flex: 1 }} />
        <button
          style={S.button}
          disabled={!selected || chosen.size === 0}
          onClick={() => {
            const picked = targets.filter((t) => chosen.has(targetId(t)));
            startRun({
              suite_id: selected,
              targets: picked.map((t) => ({
                provider: t.provider,
                endpoint: t.endpoint,
                model: t.model,
                // Carried through, or a local GGUF target would fall back to
                // whatever llama-server happened to have loaded — scoring the
                // wrong weights under the right name.
                model_path: t.modelPath,
                label: t.label,
              })),
              // Per suite, not one 'evals' bucket for everything: localtrack
              // charts a project's runs against each other, so pouring a
              // tool-calling suite and an MMLU benchmark into one project plots
              // two unrelated pass rates on the same axis. `suite` is the
              // selected one, so a sweep always lands beside its own history.
              localtrack_project: localtrackProject(suite),
            })
              .then((r) => setMessage(r.started ? 'Sweep started.' : r.message))
              .catch((e) => setMessage(String(e)));
          }}
        >
          Start sweep
        </button>
      </div>
      <div style={S.scroll}>
        <div style={S.sectionHead}>
          <span style={S.heading}>Models</span>
          <span style={S.mono}>
            {chosen.size} of {targets.length} selected
          </span>
        </div>
        {targets.length === 0 && <div style={S.mono}>No models resolved on this node.</div>}
        {/* A grid, not a column of checkboxes: these are peers being chosen
            between for one sweep, and the stacked-label form is exactly the
            shape that reads as an unstyled form rather than a loadout. */}
        <DataList layout="grid" label="Models to sweep">
          {targets.map((t, i) => (
            <PickRow
              key={targetId(t)}
              index={i}
              title={t.label}
              meta={
                <>
                  <span>{t.provider}</span>
                  <span>{t.source}</span>
                  {t.architecture && <span>{t.architecture}</span>}
                  {t.loaded && <span>loaded</span>}
                </>
              }
              checked={chosen.has(targetId(t))}
              onChange={(on) => {
                const next = new Set(chosen);
                if (on) next.add(targetId(t));
                else next.delete(targetId(t));
                setChosen(next);
              }}
            />
          ))}
        </DataList>
        {message && <div style={{ ...S.mono, marginTop: 10 }}>{message}</div>}

        {sweeps.length > 0 && (
          <>
            <div style={{ ...S.sectionHead, marginTop: 16 }}>
              <span style={S.heading}>Running now</span>
            </div>
            <DataList label="Running sweeps">
              {sweeps.map((sw, i) => (
                <DataRow
                  key={sw.key}
                  index={i}
                  title={sw.targets.join(', ') || sw.suiteId}
                  kind="info"
                  meta={[sw.startedAt, `${sw.targets.length} target(s)`]}
                  actions={
                    <button
                      style={S.button}
                      onClick={() =>
                        cancelSweep(sw.key)
                          .then(() => {
                            // Says what survives, because "stop" on a half-finished
                            // sweep reads as "throw the results away" otherwise.
                            setMessage('Sweep stopped. Targets it finished keep their results.');
                            reload();
                          })
                          .catch((e) => setMessage(String(e)))
                      }
                    >
                      Stop
                    </button>
                  }
                >
                  {sw.suiteId}
                </DataRow>
              ))}
            </DataList>
          </>
        )}

        <div style={{ ...S.sectionHead, marginTop: 16 }}>
          <span style={S.heading}>Recent runs</span>
        </div>
        <DataList label="Recent runs">
          {runs.map((r, i) => (
            <DataRow
              key={r.id}
              index={i}
              title={r.label}
              kind={runKind(r)}
              meta={[
                r.status,
                <>
                  <em>
                    <RollingNumber value={r.passed} />
                  </em>
                  /{r.completed || r.total}
                </>,
                pct(r),
              ]}
              metaTone={r.error ? 'fail' : undefined}
              footnotes={
                r.error ? (
                  <div style={{ ...S.mono, color: 'var(--danger, #e06c75)' }}>{r.error}</div>
                ) : undefined
              }
            />
          ))}
        </DataList>
      </div>
    </>
  );
}

/** Results: the scoreboard, failures first. */
function Results({ selected }: { selected: string }) {
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [runId, setRunId] = useState('');
  const [results, setResults] = useState<CaseResult[]>([]);
  const [showPasses, setShowPasses] = useState(false);

  // Keyed on the suite alone. `runId` was in this dep array *and* set inside it,
  // which re-listed the runs on every pick — and because the guard was `!runId`,
  // switching suite left the previous suite's run selected with its results still
  // on screen. Reconciling against the list that just arrived does both jobs: keep
  // the current pick if this suite still has it, otherwise fall to the newest.
  useEffect(() => {
    listRuns(selected).then((rs) => {
      setRuns(rs);
      setRunId((current) => (rs.some((r) => r.id === current) ? current : (rs[0]?.id ?? '')));
    });
  }, [selected]);

  useEffect(() => {
    // Cleared rather than left standing: a suite with no runs would otherwise keep
    // showing the last suite's rows under an empty selector.
    if (!runId) {
      setResults([]);
      return;
    }
    getRun(runId).then((r) => setResults(r.results));
  }, [runId]);

  const shown = useMemo(
    () => (showPasses ? results : results.filter((r) => !r.passed)),
    [results, showPasses],
  );
  const run = runs.find((r) => r.id === runId);

  return (
    <>
      <div style={S.bar}>
        <span style={S.heading}>Results</span>
        <select style={S.select} value={runId} onChange={(e) => setRunId(e.target.value)}>
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              {r.label} — {r.passed}/{r.total}
            </option>
          ))}
        </select>
        <button style={S.button} onClick={() => setShowPasses((v) => !v)}>
          {showPasses ? 'Failures only' : 'Show passes'}
        </button>
        <span style={{ flex: 1 }} />
        {run && <span style={S.mono}>{pct(run)} pass</span>}
      </div>
      <div style={S.scroll}>
        {shown.length === 0 && (
          <div style={S.mono}>
            {results.length ? 'Everything passed.' : 'No results for this run yet.'}
          </div>
        )}
        <DataList label="Case results">
          {shown.map((r, i) => (
            <DataRow
              key={r.case_id}
              index={i}
              title={r.case_id}
              kind={r.passed ? 'ok' : 'fail'}
              meta={[
                r.grade,
                <>
                  <em>{r.rounds}</em> rounds
                </>,
                <>
                  <em>{r.tools_offered}</em> tools
                </>,
                `${Math.round(r.duration_ms)}ms`,
              ]}
              // A dropped tool is not a failure of the model, so it must not be the
              // row's verdict — but it is often the reason a failure happened, so it
              // tints the figures rather than sitting silently below them.
              metaTone={r.tools_dropped.length > 0 ? 'warn' : undefined}
              badge={r.tools_dropped.length > 0 ? 'budget' : undefined}
              footnotes={
                <>
                  {!r.passed && (
                    <div style={S.mono}>
                      expected {describeCalls(r.expected)} · actual {describeCalls(r.actual)}
                    </div>
                  )}
                  {r.tools_dropped.length > 0 && (
                    <div style={{ ...S.mono, color: 'var(--warn, #e2c08d)' }}>
                      {r.tools_dropped.length} tool(s) dropped by the budget — this model never saw{' '}
                      {r.tools_dropped.slice(0, 3).join(', ')}
                    </div>
                  )}
                  {r.groups_loaded.length > 0 && (
                    <div style={S.mono}>loaded {r.groups_loaded.join(', ')}</div>
                  )}
                  {/* The recorder's turn id. Shown rather than kept in the database
                      because it is the handle on the exact prompt and tool schemas
                      that went out for this case — without it on screen there is no
                      way to get from a puzzling row to what the model was actually
                      given. */}
                  {r.turn_id && (
                    <div style={{ ...S.mono, color: 'var(--text-dim, #8b949e)' }}>
                      turn {r.turn_id}
                    </div>
                  )}
                </>
              }
            >
              {/* The detail line first: it is the sentence that says what to do. */}
              {r.detail}
            </DataRow>
          ))}
        </DataList>
      </div>
    </>
  );
}

export function EvalsHub() {
  const { section } = usePaneSection();
  const [suites, setSuites] = useState<EvalSuite[]>([]);
  const [selected, setSelected] = useState('');

  const reload = useCallback(() => {
    listSuites().then((s) => {
      setSuites(s);
      setSelected((cur) => cur || s[0]?.id || '');
    });
  }, []);

  useEffect(reload, [reload]);

  return (
    <div style={S.pane}>
      {section === 'run' && <Run suites={suites} selected={selected} />}
      {section === 'results' && <Results selected={selected} />}
      {section === 'compare' && <Compare selected={selected} />}
      {(!section || section === 'suites') && (
        <Suites suites={suites} selected={selected} onSelect={setSelected} onReload={reload} />
      )}
    </div>
  );
}
