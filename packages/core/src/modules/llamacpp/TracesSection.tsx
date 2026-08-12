import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  deleteTrace,
  estimateTrace,
  formatBytes,
  getRecordValues,
  getTrace,
  listTraces,
  runTrace,
  type ModelEntry,
  type Progress,
  type RecordValues,
  type TraceDetail,
  type TraceEstimate,
  type TraceListResponse,
  type TraceRecord,
} from './api';

/**
 * The scrubber: snapshot traces of a forward pass, read back off disk.
 *
 * There is no live tensor stream and there deliberately never will be — a single
 * traced pass with attention on is around a gigabyte, so the run writes a
 * snapshot and this pane scrubs it afterwards. That is also what makes a trace
 * something you can keep and compare rather than something you have to catch.
 *
 * Two honesty rules from the backend are rendered rather than smoothed over:
 * every record shows its `fidelity` (a `summary` shows statistics and *no*
 * values, because it has none), and the provenance line shows which libllama
 * produced the trace — which is not the `llama-server` build the chat path runs,
 * and is why a trace may not be overlaid on a turn unless both agree.
 */

/** Downsample to something a browser can lay out; a residual is ~4096 floats. */
const CELLS = 192;

function buckets(values: number[]): number[] {
  if (values.length <= CELLS) return values;
  const size = values.length / CELLS;
  const out: number[] = [];
  for (let i = 0; i < CELLS; i += 1) {
    const start = Math.floor(i * size);
    const end = Math.max(start + 1, Math.floor((i + 1) * size));
    let total = 0;
    for (let j = start; j < end; j += 1) total += values[j] ?? 0;
    out.push(total / (end - start));
  }
  return out;
}

/**
 * A diverging ramp centred on zero. Activations are signed and the sign is the
 * interesting part, so a sequential ramp would hide half the story.
 */
function cellColor(value: number, scale: number): string {
  if (!scale) return 'rgb(120 120 120 / 25%)';
  const t = Math.max(-1, Math.min(1, value / scale));
  const alpha = 0.12 + Math.abs(t) * 0.8;
  return t >= 0 ? `rgb(110 190 255 / ${alpha})` : `rgb(255 140 120 / ${alpha})`;
}

function ValueStrip({ data }: { data: RecordValues }) {
  const cells = useMemo(() => buckets(data.values), [data.values]);
  const scale = useMemo(() => cells.reduce((max, v) => Math.max(max, Math.abs(v)), 0), [cells]);
  if (!data.values.length) {
    return (
      <p className="llama-note">
        This record was stored as a <strong>summary</strong> — statistics instead of the tensor.
        There are no values to draw.
      </p>
    );
  }
  return (
    <div className="llama-heat" role="img" aria-label={`${data.record.name} activations`}>
      {cells.map((value, index) => (
        <span
          key={index}
          className="llama-heat-cell"
          style={{ background: cellColor(value, scale) }}
          title={value.toFixed(4)}
        />
      ))}
    </div>
  );
}

function Stats({ summary }: { summary: Record<string, number> }) {
  const keys = Object.keys(summary);
  if (!keys.length) return null;
  return (
    <div className="llama-row llama-meta">
      {keys.map((key) => (
        <span key={key}>
          {key} <code>{summary[key]?.toFixed(4)}</code>
        </span>
      ))}
    </div>
  );
}

function RecordList({
  records,
  selected,
  onSelect,
}: {
  records: TraceRecord[];
  selected: number;
  onSelect: (index: number) => void;
}) {
  const [pass, setPass] = useState(0);
  const passes = useMemo(
    () => Array.from(new Set(records.map((r) => r.passIndex))).sort((a, b) => a - b),
    [records],
  );
  const shown = records.filter((r) => r.passIndex === pass);

  return (
    <div className="llama-card">
      <h3>Nodes</h3>
      {passes.length > 1 && (
        <div className="llama-row">
          <label>
            Pass
            <select value={pass} onChange={(e) => setPass(Number(e.target.value))}>
              {passes.map((p) => (
                <option key={p} value={p}>
                  {p === 0 ? 'prompt' : `token ${p}`}
                </option>
              ))}
            </select>
          </label>
          <span className="llama-meta">
            Each generated token is its own forward pass and its own set of nodes.
          </span>
        </div>
      )}
      <ul className="llama-records">
        {shown.map((record) => (
          <li key={record.index}>
            <button
              className={`llama-record${record.index === selected ? ' llama-record-on' : ''}`}
              onClick={() => onSelect(record.index)}
            >
              <span className="llama-record-name">{record.name}</span>
              <span className="llama-meta">
                {record.ne.filter((n) => n > 1).join('×') || '1'} · {record.op.toLowerCase()}
              </span>
              <span className={`llama-tag llama-fidelity-${record.fidelity}`}>
                {record.fidelity}
              </span>
            </button>
          </li>
        ))}
        {!shown.length && <li className="llama-meta">No nodes captured in this pass.</li>}
      </ul>
    </div>
  );
}

function TraceView({ traceId }: { traceId: string }) {
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [selected, setSelected] = useState(0);
  const [values, setValues] = useState<RecordValues | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setDetail(null);
    setSelected(0);
    setValues(null);
    let alive = true;
    void getTrace(traceId)
      .then((d) => alive && setDetail(d))
      .catch((err) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
  }, [traceId]);

  useEffect(() => {
    if (!detail?.records.length) return;
    let alive = true;
    void getRecordValues(traceId, selected)
      .then((v) => alive && setValues(v))
      .catch(() => alive && setValues(null));
    return () => {
      alive = false;
    };
  }, [traceId, selected, detail]);

  if (error) return <p className="llama-error">{error}</p>;
  if (!detail) return <p className="llama-meta">Loading…</p>;
  const trace = detail.trace;

  return (
    <div className="llama-section">
      <div className="llama-card">
        <h3>{trace.modelName}</h3>
        <div className="llama-row">
          <span className="llama-tag">{trace.recordCount} nodes</span>
          <span className="llama-tag">{formatBytes(trace.diskBytes)}</span>
          <span className="llama-tag">{trace.fidelity}</span>
          {trace.attention && <span className="llama-tag">attention</span>}
          {/* Flash attention fuses the scores into one kernel, so the matrix
              never exists as a node. A trace with it on could not have captured
              attention at all, which is worth stating rather than implying. */}
          <span className="llama-tag">{trace.flashAttn ? 'flash-attn on' : 'flash-attn off'}</span>
        </div>
        <p className="llama-meta">
          Traced by <code>{trace.llamaBuild}</code>. This is the wheel&rsquo;s libllama, not the{' '}
          <code>llama-server</code> build the chat path runs — a trace and a chat turn are different
          runs unless both the build and the model match.
        </p>
        <p className="llama-meta">
          <code>modelSha</code> {trace.modelSha.slice(0, 12)} ({trace.modelShaScope})
        </p>
        {!trace.chatTemplate && <p className="llama-note">{trace.note}</p>}
        <div className="llama-tokens">
          {detail.tokens.map((token) => (
            <span
              key={token.index}
              className={`llama-token${token.generated ? ' llama-token-gen' : ''}`}
              title={`#${token.index} · id ${token.id}`}
            >
              {token.text || '␣'}
            </span>
          ))}
        </div>
      </div>

      <RecordList records={detail.records} selected={selected} onSelect={setSelected} />

      {values && (
        <div className="llama-card">
          <h3>{values.record.name}</h3>
          <div className="llama-row llama-meta">
            <span>dtype {values.record.dtype}</span>
            <span>shape {values.record.ne.join('×')}</span>
            {values.record.layer !== null && <span>layer {values.record.layer}</span>}
            {values.truncated && <span>first {values.values.length} values</span>}
          </div>
          <ValueStrip data={values} />
          <Stats summary={values.summary} />
        </div>
      )}
    </div>
  );
}

function NewTrace({ models, onDone }: { models: ModelEntry[]; onDone: (traceId: string) => void }) {
  const [modelPath, setModelPath] = useState('');
  const [prompt, setPrompt] = useState('The capital of France is');
  const [maxTokens, setMaxTokens] = useState(0);
  const [attention, setAttention] = useState(false);
  const [fidelity, setFidelity] = useState('fp16');
  const [estimate, setEstimate] = useState<TraceEstimate | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const path = modelPath || models[0]?.path || '';

  // A progress bar that has already started is too late: the difference between
  // a 200 MB trace and a 12 GB one is the attention checkbox, so the estimate
  // follows the form rather than waiting for a button.
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      void estimateTrace({ modelPath: path, prompt, maxTokens, attention, fidelity })
        .then((e) => alive && setEstimate(e))
        .catch(() => alive && setEstimate(null));
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [path, prompt, maxTokens, attention, fidelity]);

  const run = async () => {
    if (!path) return;
    setBusy(true);
    setError('');
    setProgress({ status: 'starting' });
    let traceId = '';
    try {
      await runTrace({ modelPath: path, prompt, maxTokens, attention, fidelity }, (p) => {
        if (p.error) setError(String(p.error));
        else {
          if (typeof p.traceId === 'string') traceId = p.traceId;
          setProgress(p);
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setProgress(null);
      onDone(traceId);
    }
  };

  const overBudget = !!estimate && estimate.bytes > estimate.budgetBytes;

  return (
    <div className="llama-card">
      <h3>New trace</h3>
      <div className="llama-row">
        <label>
          Model
          <select value={path} onChange={(e) => setModelPath(e.target.value)}>
            {models.map((m) => (
              <option key={m.path} value={m.path}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <textarea
        className="llama-prompt"
        rows={3}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="Raw text — no chat template is applied"
      />
      <div className="llama-row">
        <label>
          Generate
          <input
            type="number"
            min={0}
            max={32}
            value={maxTokens}
            onChange={(e) => setMaxTokens(Number(e.target.value))}
          />
        </label>
        <label>
          Fidelity
          <select value={fidelity} onChange={(e) => setFidelity(e.target.value)}>
            <option value="fp16">fp16</option>
            <option value="full">full (fp32)</option>
            <option value="summary">summary only</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={attention}
            onChange={(e) => setAttention(e.target.checked)}
          />
          Attention scores
        </label>
      </div>
      {estimate &&
        (estimate.error ? (
          <p className="llama-error">{estimate.error}</p>
        ) : (
          <p className={overBudget ? 'llama-error' : 'llama-meta'}>
            Estimate: {formatBytes(estimate.bytes)}, ~{estimate.seconds}s over {estimate.layers}{' '}
            layers × {estimate.embeddingLength} wide.{' '}
            {overBudget
              ? `That is over the ${formatBytes(estimate.budgetBytes)} trace budget — older traces will be pruned.`
              : estimate.note}
          </p>
        ))}
      {progress && <p className="llama-meta">{progress.status ?? 'working'}…</p>}
      {error && <p className="llama-error">{error}</p>}
      <div className="llama-row">
        <button onClick={() => void run()} disabled={busy || !path}>
          {busy ? 'Tracing…' : 'Run trace'}
        </button>
        <span className="llama-meta">
          Runs in a subprocess: a crash inside a ggml callback is an exit code, not a dead backend.
        </span>
      </div>
    </div>
  );
}

export function TracesSection({ models }: { models: ModelEntry[] }) {
  const [list, setList] = useState<TraceListResponse | null>(null);
  const [selected, setSelected] = useState('');

  const refresh = useCallback(() => {
    void listTraces()
      .then((l) => {
        setList(l);
        setSelected((current) =>
          current && l.traces.some((t) => t.traceId === current)
            ? current
            : (l.traces[0]?.traceId ?? ''),
        );
      })
      .catch(() => undefined);
  }, []);

  useEffect(refresh, [refresh]);

  const remove = async (traceId: string) => {
    await deleteTrace(traceId);
    refresh();
  };

  return (
    <div className="llama-section">
      {list && !list.available && (
        <div className="llama-card">
          <h3>Activations are unavailable here</h3>
          <p className="llama-note">{list.reason}</p>
          <p className="llama-meta">
            The chat path is unaffected — it runs a downloaded <code>llama-server</code> binary and
            needs none of this.
          </p>
        </div>
      )}

      <NewTrace
        models={models}
        onDone={() => {
          refresh();
        }}
      />

      <div className="llama-card">
        <h3>
          Traces{' '}
          <span className="llama-meta">
            {formatBytes(list?.usedBytes ?? 0)} of {formatBytes(list?.budgetBytes ?? 0)}
          </span>
        </h3>
        <ul className="llama-models">
          {(list?.traces ?? []).map((trace) => (
            <li key={trace.traceId}>
              <div className="llama-model-head">
                <button
                  className={`llama-record${trace.traceId === selected ? ' llama-record-on' : ''}`}
                  onClick={() => setSelected(trace.traceId)}
                >
                  <span className="llama-record-name">{trace.modelName}</span>
                  <span className="llama-meta">
                    {new Date((trace.createdAt ?? 0) * 1000).toLocaleString()} · {trace.recordCount}{' '}
                    nodes · {formatBytes(trace.diskBytes)}
                  </span>
                </button>
                <button className="llama-danger" onClick={() => void remove(trace.traceId)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
          {!list?.traces.length && (
            <li className="llama-meta">
              No traces yet. A trace is a snapshot of one forward pass — run one above, then scrub
              it here.
            </li>
          )}
        </ul>
      </div>

      {selected && <TraceView traceId={selected} />}
    </div>
  );
}
