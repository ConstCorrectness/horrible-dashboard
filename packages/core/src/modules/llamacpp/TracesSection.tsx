import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  deleteTrace,
  estimateTrace,
  formatBytes,
  getCaptureSets,
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
  getTraceSeries,
  type TraceRecord,
  type TraceSeries,
  type CaptureSet,
} from './api';
import { KIND_LABELS, kindsPresent, nodeKind, type NodeKind } from './node-kind';
import { addPin, addPins, clearPins, getPins, MAX_PINS, removePin } from './pins';
import { subscribeTracePrompt, takePendingPrompt } from './trace-prompt';

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

/**
 * A watched node's statistic across passes.
 *
 * A **gap is drawn as a gap**: a pass whose record was summarized without a stored
 * statistic has nothing to report, and joining the line across it would draw a
 * measurement that was never taken. So the polyline is split into runs of adjacent
 * measured points, and a lone measured point between two gaps is a dot.
 */
function Sparkline({ series }: { series: TraceSeries }) {
  const points = series.points;
  const measured = points.filter((p) => p.value !== null);
  if (measured.length < 1) return <span className="llama-meta">no series</span>;

  const width = 96;
  const height = 18;
  const lo = Math.min(...measured.map((p) => p.value as number));
  const hi = Math.max(...measured.map((p) => p.value as number));
  const span = hi - lo || 1;
  const lastPass = points[points.length - 1]?.passIndex || 1;
  const x = (pass: number) => (lastPass ? (pass / lastPass) * (width - 2) + 1 : width / 2);
  const y = (value: number) => height - 1 - ((value - lo) / span) * (height - 2);

  const runs: string[][] = [];
  let run: string[] = [];
  for (const point of points) {
    if (point.value === null) {
      if (run.length) runs.push(run);
      run = [];
      continue;
    }
    run.push(`${x(point.passIndex).toFixed(1)},${y(point.value).toFixed(1)}`);
  }
  if (run.length) runs.push(run);

  return (
    <svg
      className="llama-spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${series.name} ${series.stat} over ${points.length} passes`}
    >
      {runs.map((coords, index) =>
        coords.length > 1 ? (
          <polyline key={index} className="llama-spark-line" points={coords.join(' ')} />
        ) : (
          <circle
            key={index}
            className="llama-spark-dot"
            cx={coords[0].split(',')[0]}
            cy={coords[0].split(',')[1]}
            r={1.4}
          />
        ),
      )}
    </svg>
  );
}

/**
 * The watch window: pinned nodes, all visible at once, re-resolved for the pass
 * being viewed.
 *
 * A pin holds a **name**, so switching pass keeps the watch and shows that pass's
 * record. A name this trace never captured is shown as **unresolved** rather than
 * dropped — that state is informative (it is what a capture set that skipped the
 * node looks like), and silently shrinking the list would hide it.
 */
function WatchCard({
  pins,
  records,
  values,
  series,
  pass,
  onUnpin,
  onClear,
  onSelect,
}: {
  pins: string[];
  records: TraceRecord[];
  values: Map<string, RecordValues>;
  series: Map<string, TraceSeries>;
  pass: number;
  onUnpin: (name: string) => void;
  onClear: () => void;
  onSelect: (index: number) => void;
}) {
  if (!pins.length) return null;
  return (
    <div className="llama-card">
      <h3>
        Watch <span className="llama-meta">{pins.length} pinned</span>
        <button className="llama-linkbtn" onClick={onClear}>
          Clear
        </button>
      </h3>
      <ul className="llama-watch">
        {pins.map((name) => {
          const record = records.find((r) => r.name === name && r.passIndex === pass);
          const data = values.get(name);
          const line = series.get(name);
          return (
            <li key={name} className={`llama-watch-row${record ? '' : ' llama-watch-missing'}`}>
              <div className="llama-watch-head">
                <button
                  className="llama-watch-name"
                  onClick={() => record && onSelect(record.index)}
                  disabled={!record}
                  title={record ? 'Show this node below' : undefined}
                >
                  {name}
                </button>
                {record ? (
                  <>
                    <span className="llama-meta">
                      {record.ne.filter((n) => n > 1).join('×') || '1'}
                    </span>
                    <span className={`llama-tag llama-fidelity-${record.fidelity}`}>
                      {record.fidelity}
                    </span>
                  </>
                ) : (
                  <span className="llama-meta">not captured in this trace</span>
                )}
                {line && <Sparkline series={line} />}
                <button
                  className="llama-linkbtn"
                  onClick={() => onUnpin(name)}
                  title="Stop watching"
                >
                  ✕
                </button>
              </div>
              {data && data.values.length > 0 && <ValueStrip data={data} />}
              {data && <Stats summary={data.summary} />}
            </li>
          );
        })}
      </ul>
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

/** The filter state, all composing. `layerLo`/`layerHi` are null while untouched,
 * which is a different thing from covering the full range — see `matches`. */
interface NodeFilterState {
  query: string;
  kinds: Set<NodeKind>;
  layerLo: number | null;
  layerHi: number | null;
}

const EMPTY_FILTER: NodeFilterState = {
  query: '',
  kinds: new Set(),
  layerLo: null,
  layerHi: null,
};

/**
 * Whether a record survives the filter.
 *
 * The subtle rule is the layerless node (`inp_embd`, `result_norm`, `result_output`):
 * it is kept while the layer range is **untouched** and hidden once the range is
 * narrowed. That mirrors `TraceFilter.wanted` in the tracer, which keeps a node with
 * no block index rather than letting a layer selection silently drop the embedding
 * and the output head — the two nodes you are most likely to want and least likely
 * to notice missing.
 */
function matches(record: TraceRecord, filter: NodeFilterState): boolean {
  if (filter.query && !record.name.toLowerCase().includes(filter.query.toLowerCase())) return false;
  if (filter.kinds.size && !filter.kinds.has(nodeKind(record.name))) return false;
  const ranged = filter.layerLo !== null || filter.layerHi !== null;
  if (!ranged) return true;
  if (record.layer === null) return false;
  if (filter.layerLo !== null && record.layer < filter.layerLo) return false;
  if (filter.layerHi !== null && record.layer > filter.layerHi) return false;
  return true;
}

function NodeFilterBar({
  filter,
  setFilter,
  kinds,
  layerMax,
  shown,
  total,
}: {
  filter: NodeFilterState;
  setFilter: (next: NodeFilterState) => void;
  kinds: NodeKind[];
  layerMax: number | null;
  shown: number;
  total: number;
}) {
  const toggle = (kind: NodeKind) => {
    const next = new Set(filter.kinds);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    setFilter({ ...filter, kinds: next });
  };
  const num = (raw: string): number | null => (raw === '' ? null : Number(raw));

  return (
    <div className="llama-filter">
      <div className="llama-row">
        <input
          className="llama-filter-search"
          value={filter.query}
          placeholder="Filter by name — kq, ffn_out, -41"
          onChange={(e) => setFilter({ ...filter, query: e.target.value })}
        />
        {layerMax !== null && (
          <label title="Blocks to show. Leave empty for all; narrowing hides the nodes outside the stack (inp_embd, result_norm).">
            Layers
            <input
              type="number"
              className="llama-filter-layer"
              min={0}
              max={layerMax}
              placeholder="0"
              value={filter.layerLo ?? ''}
              onChange={(e) => setFilter({ ...filter, layerLo: num(e.target.value) })}
            />
            <span className="llama-meta">–</span>
            <input
              type="number"
              className="llama-filter-layer"
              min={0}
              max={layerMax}
              placeholder={String(layerMax)}
              value={filter.layerHi ?? ''}
              onChange={(e) => setFilter({ ...filter, layerHi: num(e.target.value) })}
            />
          </label>
        )}
        <span className="llama-meta">
          {shown} of {total}
        </span>
      </div>
      {/* Only the kinds this trace actually contains: a Mamba trace showing an
          "attention" toggle that matches nothing is worse than no toggle. */}
      <div className="llama-filter-kinds">
        {kinds.map((kind) => (
          <button
            key={kind}
            className={`llama-chip${filter.kinds.has(kind) ? ' llama-chip-on' : ''}`}
            onClick={() => toggle(kind)}
          >
            {KIND_LABELS[kind]}
          </button>
        ))}
        {(filter.query ||
          filter.kinds.size ||
          filter.layerLo !== null ||
          filter.layerHi !== null) && (
          <button className="llama-linkbtn" onClick={() => setFilter(EMPTY_FILTER)}>
            Reset
          </button>
        )}
      </div>
    </div>
  );
}

function RecordList({
  records,
  selected,
  pass,
  passes,
  onPass,
  pins,
  onPin,
  onUnpin,
  onPinLayer,
  onSelect,
}: {
  records: TraceRecord[];
  selected: number;
  pass: number;
  passes: number[];
  onPass: (pass: number) => void;
  pins: string[];
  onPin: (name: string) => void;
  onUnpin: (name: string) => void;
  onPinLayer: (names: string[]) => void;
  onSelect: (index: number) => void;
}) {
  const [filter, setFilter] = useState<NodeFilterState>(EMPTY_FILTER);
  const inPass = useMemo(() => records.filter((r) => r.passIndex === pass), [records, pass]);
  const kinds = useMemo(() => kindsPresent(inPass.map((r) => r.name)), [inPass]);
  const layerMax = useMemo(() => {
    const layers = inPass.map((r) => r.layer).filter((l): l is number => l !== null);
    return layers.length ? Math.max(...layers) : null;
  }, [inPass]);
  const shown = useMemo(() => inPass.filter((r) => matches(r, filter)), [inPass, filter]);

  // Grouped by block so "pin this whole layer" has something to hang off, and so a
  // 300-row list reads as 42 blocks rather than one undifferentiated scroll.
  const groups = useMemo(() => {
    const byLayer = new Map<number | null, TraceRecord[]>();
    for (const record of shown) {
      const list = byLayer.get(record.layer);
      if (list) list.push(record);
      else byLayer.set(record.layer, [record]);
    }
    return [...byLayer.entries()];
  }, [shown]);

  const full = pins.length >= MAX_PINS;

  return (
    <div className="llama-card">
      <h3>Nodes</h3>
      {passes.length > 1 && (
        <div className="llama-row">
          <label>
            Pass
            <select value={pass} onChange={(e) => onPass(Number(e.target.value))}>
              {passes.map((p) => (
                <option key={p} value={p}>
                  {p === 0 ? 'prompt' : `token ${p}`}
                </option>
              ))}
            </select>
          </label>
          <span className="llama-meta">
            Each generated token is its own forward pass and its own set of nodes. Pinned nodes
            follow you across them.
          </span>
        </div>
      )}
      <NodeFilterBar
        filter={filter}
        setFilter={setFilter}
        kinds={kinds}
        layerMax={layerMax}
        shown={shown.length}
        total={inPass.length}
      />
      {full && (
        <p className="llama-note">
          {MAX_PINS} watches is the limit — each one is a request every time you change pass. Unpin
          something to add another.
        </p>
      )}
      <ul className="llama-records">
        {groups.map(([layer, group]) => (
          <li key={layer ?? 'none'}>
            <div className="llama-group">
              <span className="llama-meta">
                {layer === null ? 'outside the stack' : `blk ${layer}`}
              </span>
              <button
                className="llama-linkbtn"
                onClick={() => onPinLayer(group.map((r) => r.name))}
                disabled={full}
                title="Watch every node shown in this block"
              >
                pin all
              </button>
            </div>
            <ul className="llama-records">
              {group.map((record) => {
                const pinned = pins.includes(record.name);
                return (
                  <li key={record.index} className="llama-record-row">
                    <button
                      className={`llama-record${record.index === selected ? ' llama-record-on' : ''}`}
                      onClick={() => onSelect(record.index)}
                    >
                      <span className="llama-record-name">{record.name}</span>
                      <span className="llama-meta">
                        {record.ne.filter((n) => n > 1).join('×') || '1'} ·{' '}
                        {record.op.toLowerCase()}
                      </span>
                      <span className={`llama-tag llama-fidelity-${record.fidelity}`}>
                        {record.fidelity}
                      </span>
                    </button>
                    <button
                      className={`llama-pin${pinned ? ' llama-pin-on' : ''}`}
                      onClick={() => (pinned ? onUnpin(record.name) : onPin(record.name))}
                      disabled={!pinned && full}
                      title={pinned ? 'Stop watching' : 'Watch this node across passes'}
                      aria-pressed={pinned}
                    >
                      {pinned ? '📌' : '📍'}
                    </button>
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
        {!shown.length && (
          <li className="llama-meta">
            {inPass.length ? 'No nodes match this filter.' : 'No nodes captured in this pass.'}
          </li>
        )}
      </ul>
    </div>
  );
}

function TraceView({ traceId }: { traceId: string }) {
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [selected, setSelected] = useState(0);
  const [pass, setPass] = useState(0);
  const [values, setValues] = useState<RecordValues | null>(null);
  const [pins, setPins] = useState<string[]>([]);
  const [pinValues, setPinValues] = useState<Map<string, RecordValues>>(new Map());
  const [series, setSeries] = useState<Map<string, TraceSeries>>(new Map());
  const [error, setError] = useState('');

  // Pins are keyed by model, not by trace: a watch is about the model's structure,
  // so opening another trace of the same model re-arms the same list.
  const model = detail?.trace.modelName ?? '';

  useEffect(() => {
    setDetail(null);
    setSelected(0);
    setPass(0);
    setValues(null);
    setPinValues(new Map());
    setSeries(new Map());
    let alive = true;
    void getTrace(traceId)
      .then((d) => alive && setDetail(d))
      .catch((err) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
  }, [traceId]);

  useEffect(() => {
    setPins(getPins(model));
  }, [model]);

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

  // Watched values, re-resolved for the pass on screen. A pin naming a node this
  // trace never captured simply has no entry — the card renders that as unresolved.
  const records = detail?.records;
  useEffect(() => {
    if (!records) return;
    let alive = true;
    const wanted = pins
      .map((name) => [name, records.find((r) => r.name === name && r.passIndex === pass)] as const)
      .filter((entry): entry is [string, TraceRecord] => entry[1] !== undefined);
    void Promise.all(
      wanted.map(([name, record]) =>
        getRecordValues(traceId, record.index)
          .then((v) => [name, v] as const)
          .catch(() => null),
      ),
    ).then((results) => {
      if (!alive) return;
      setPinValues(
        new Map(results.filter((r): r is readonly [string, RecordValues] => r !== null)),
      );
    });
    return () => {
      alive = false;
    };
  }, [traceId, pins, pass, records]);

  // The sparkline is *about* generation, so it does not depend on the pass being
  // viewed — refetched only when the trace or the watch list changes.
  useEffect(() => {
    if (!records) return;
    let alive = true;
    void Promise.all(
      pins.map((name) =>
        getTraceSeries(traceId, name)
          .then((s) => [name, s] as const)
          .catch(() => null),
      ),
    ).then((results) => {
      if (!alive) return;
      setSeries(new Map(results.filter((r): r is readonly [string, TraceSeries] => r !== null)));
    });
    return () => {
      alive = false;
    };
  }, [traceId, pins, records]);

  const passes = useMemo(
    () => Array.from(new Set(detail?.records.map((r) => r.passIndex) ?? [])).sort((a, b) => a - b),
    [detail],
  );

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

      <WatchCard
        pins={pins}
        records={detail.records}
        values={pinValues}
        series={series}
        pass={pass}
        onUnpin={(name) => setPins(removePin(model, name))}
        onClear={() => setPins(clearPins(model))}
        onSelect={setSelected}
      />

      <RecordList
        records={detail.records}
        selected={selected}
        pass={pass}
        passes={passes}
        onPass={setPass}
        pins={pins}
        onPin={(name) => setPins(addPin(model, name))}
        onUnpin={(name) => setPins(removePin(model, name))}
        onPinLayer={(names) => setPins(addPins(model, names))}
        onSelect={setSelected}
      />

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
  // A prompt pushed by `llamacpp.traceSelection` before this section mounted is
  // read here; one pushed while it is already open arrives on the subscription
  // below. See trace-prompt.ts for why both are needed.
  const initial = useState(takePendingPrompt)[0];
  const [prompt, setPrompt] = useState(initial?.prompt ?? 'The capital of France is');
  const [source, setSource] = useState<string | null>(initial?.label ?? null);
  const [maxTokens, setMaxTokens] = useState(0);
  const [attention, setAttention] = useState(false);
  const [fidelity, setFidelity] = useState('fp16');
  const [captureId, setCaptureId] = useState('default');
  const [captureSets, setCaptureSets] = useState<CaptureSet[]>([]);
  const [estimate, setEstimate] = useState<TraceEstimate | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const path = modelPath || models[0]?.path || '';

  // The capture sets come from the backend rather than being restated here: two
  // lists of ggml node names in two languages is one upstream rename away from a
  // capture set that matches nothing and fails silently. Same reason `plane_order`
  // is served.
  useEffect(() => {
    let alive = true;
    void getCaptureSets()
      .then((res) => alive && setCaptureSets(res.sets))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const capture = captureSets.find((set) => set.id === captureId)?.patterns ?? [];

  // `llamacpp.traceSelection` firing while this section is already open.
  useEffect(
    () =>
      subscribeTracePrompt((next) => {
        setPrompt(next.prompt);
        setSource(next.label);
      }),
    [],
  );

  // A progress bar that has already started is too late: the difference between
  // a 200 MB trace and a 12 GB one is the attention checkbox, so the estimate
  // follows the form rather than waiting for a button.
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      void estimateTrace({ modelPath: path, prompt, maxTokens, attention, fidelity, capture })
        .then((e) => alive && setEstimate(e))
        .catch(() => alive && setEstimate(null));
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
    // `capture` is derived from `captureId`; depending on the array itself would
    // re-fire every render because a fresh array is never the same reference.
  }, [path, prompt, maxTokens, attention, fidelity, captureId, captureSets]);

  const run = async () => {
    if (!path) return;
    setBusy(true);
    setError('');
    setProgress({ status: 'starting' });
    let traceId = '';
    try {
      await runTrace({ modelPath: path, prompt, maxTokens, attention, fidelity, capture }, (p) => {
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
      {source && (
        <p className="llama-why">
          From <b>{source}</b>. Traced as raw text — no chat template — so the tokens below are
          exactly your code, not a rendered prompt around it.
        </p>
      )}
      <textarea
        className="llama-prompt"
        rows={source ? 8 : 3}
        value={prompt}
        onChange={(e) => {
          setPrompt(e.target.value);
          // Edited by hand: it is no longer the selection that was sent.
          setSource(null);
        }}
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
          Capture
          <select value={captureId} onChange={(e) => setCaptureId(e.target.value)}>
            {captureSets.map((set) => (
              <option key={set.id} value={set.id}>
                {set.label}
              </option>
            ))}
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
      {captureSets.find((set) => set.id === captureId)?.note ? (
        <p className="llama-meta">{captureSets.find((set) => set.id === captureId)?.note}</p>
      ) : null}
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
