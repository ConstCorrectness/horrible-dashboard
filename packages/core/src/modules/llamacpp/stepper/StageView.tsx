/**
 * What the stepper shows for the node under the cursor — one view per stage.
 *
 * A debugger's variable pane shows a value in the shape of its type. Here the
 * "type" is the stage, and each gets the view that answers the question you step to
 * it to ask:
 *
 * - **attention scores** (`kq_soft_max`, `kq`): which token looks at which. A strip
 *   of per-head summaries to pick from, then one head's query × key matrix, with
 *   the row of the selected position outlined.
 * - **residual** (`l_out`): what this block *wrote* — per-token relative change and
 *   turn — and what the stream now says, read through the logit lens at this layer.
 * - **moe**: which experts each token went to, and the layer × expert atlas.
 * - everything else: the tokens × features plane with each token's norm.
 *
 * Every read goes through the server's stride-aware slice. A `summary` record has
 * no values by construction and says so instead of drawing anything.
 */
import { useEffect, useMemo, useState } from 'react';

import { HeatCanvas } from '../../../viz/HeatCanvas';
import {
  getExperts,
  getLensGrid,
  getRecordHeads,
  getRecordMatrix,
  getResidualDelta,
  type ExpertRouting,
  type HeadSummary,
  type LensGrid,
  type RecordMatrix,
  type ResidualDelta,
  type TraceToken,
} from '../api';
import type { Step } from './program';

type Load<T> = { data: T | null; error: string };

function useLoad<T>(key: string, load: () => Promise<T>): Load<T> {
  const [state, setState] = useState<Load<T>>({ data: null, error: '' });
  useEffect(() => {
    let alive = true;
    setState({ data: null, error: '' });
    load()
      .then((data) => alive && setState({ data, error: '' }))
      .catch(
        (err: unknown) =>
          alive &&
          setState({ data: null, error: err instanceof Error ? err.message : String(err) }),
      );
    return () => {
      alive = false;
    };
    // `load` closes over the same inputs `key` names; keying on the string keeps
    // the effect from refiring on every render's fresh closure.
  }, [key]);
  return state;
}

export function isAttentionScores(name: string): boolean {
  return name.startsWith('kq_soft_max') || name.startsWith('kq-');
}

function tokenText(tokens: readonly TraceToken[], position: number): string {
  const t = tokens[position];
  return t ? JSON.stringify(t.text || ' ') : `#${position}`;
}

// ── generic plane ───────────────────────────────────────────────────────────

function NormBars({
  norms,
  offset,
  tokens,
  position,
  onPosition,
  label,
}: {
  norms: readonly number[];
  offset: number;
  tokens: readonly TraceToken[];
  position: number;
  onPosition: (p: number) => void;
  label: string;
}) {
  const peak = Math.max(1e-9, ...norms);
  return (
    <div className="stp-bars" role="list" aria-label={label}>
      {norms.map((v, i) => {
        const pos = offset + i;
        return (
          <button
            key={pos}
            className={`stp-bar-row${pos === position ? ' stp-bar-on' : ''}`}
            role="listitem"
            onClick={() => onPosition(pos)}
            title={`position ${pos} · ${label} ${v.toFixed(3)}`}
            style={{ animationDelay: `${Math.min(i, 12) * 18}ms` }}
          >
            <span className="stp-bar-token">{tokenText(tokens, pos)}</span>
            <span className="stp-bar-track">
              <span className="stp-bar-fill" style={{ transform: `scaleX(${v / peak})` }} />
            </span>
            <span className="stp-num">{v.toFixed(2)}</span>
          </button>
        );
      })}
    </div>
  );
}

function PlaneView({ traceId, step, tokens, position, onPosition }: ViewProps) {
  const matrix = useLoad<RecordMatrix>(`${traceId}:${step.recordIndex}:plane`, () =>
    getRecordMatrix(traceId, step.recordIndex, { cols: 192 }),
  );
  if (matrix.error) return <p className="llama-note">{matrix.error}</p>;
  if (!matrix.data) return <p className="llama-meta">Reading {step.name}…</p>;
  const m = matrix.data;
  return (
    <div className="stp-view">
      <HeatCanvas
        data={m.values}
        rows={m.rows}
        cols={m.cols}
        label={`${step.name}: ${m.rows} tokens by ${m.sourceCols} features`}
        height={Math.min(220, 18 * m.rows + 20)}
      />
      <p className="stp-caption">
        {m.rows} {m.rowAxis}s × {m.sourceCols} {m.colAxis}s
        {m.pooled ? ` · pooled to ${m.cols} columns by max |x| (signs kept)` : ''}
      </p>
      <NormBars
        norms={m.rowNorms}
        offset={m.rowOffset}
        tokens={tokens}
        position={position}
        onPosition={onPosition}
        label="‖x‖"
      />
    </div>
  );
}

// ── attention ───────────────────────────────────────────────────────────────

function AttentionView({ traceId, step, tokens, position, onPosition }: ViewProps) {
  const [head, setHead] = useState(0);
  const [hover, setHover] = useState<{ row: number; col: number; value: number | null } | null>(
    null,
  );
  const heads = useLoad<{ heads: HeadSummary[] }>(`${traceId}:${step.recordIndex}:heads`, () =>
    getRecordHeads(traceId, step.recordIndex),
  );
  const matrix = useLoad<RecordMatrix>(`${traceId}:${step.recordIndex}:h${head}`, () =>
    getRecordMatrix(traceId, step.recordIndex, { head }),
  );
  const list = heads.data?.heads ?? [];
  // The sink: the head leaning hardest on position 0 is almost always the least
  // interesting, so the strip is ordered as ggml numbers them and merely marked.
  const m = matrix.data;
  const queryRow = m ? position - m.rowOffset : -1;
  return (
    <div className="stp-view">
      {heads.error ? (
        <p className="llama-note">{heads.error}</p>
      ) : (
        <div className="stp-heads" role="listbox" aria-label="Attention heads">
          {list.map((h) => (
            <button
              key={h.head}
              role="option"
              aria-selected={h.head === head}
              className={`stp-head${h.head === head ? ' stp-head-on' : ''}`}
              onClick={() => setHead(h.head)}
              title={`head ${h.head} · entropy ${h.entropy.toFixed(2)} nats · self ${h.selfWeight.toFixed(
                2,
              )} · first-token ${h.firstWeight.toFixed(2)}`}
            >
              <span className="stp-head-id">{h.head}</span>
              <span
                className="stp-head-sink"
                style={{ height: `${Math.round(h.firstWeight * 100)}%` }}
              />
            </button>
          ))}
        </div>
      )}
      {matrix.error ? (
        <p className="llama-note">{matrix.error}</p>
      ) : !m ? (
        <p className="llama-meta">Reading head {head}…</p>
      ) : (
        <>
          <div className="stp-attn">
            <HeatCanvas
              data={m.values}
              rows={m.rows}
              cols={m.cols}
              max={Math.max(1e-9, ...m.values)}
              label={`${step.name} head ${head}: query by key attention`}
              height={Math.min(260, Math.max(90, 22 * m.rows))}
              onHover={setHover}
            />
            {queryRow >= 0 && queryRow < m.rows && (
              <span
                className="stp-attn-row"
                style={{ top: `${(queryRow / m.rows) * 100}%`, height: `${100 / m.rows}%` }}
              />
            )}
          </div>
          <p className="stp-caption">
            {hover && hover.value !== null
              ? `${tokenText(tokens, m.rowOffset + hover.row)} → ${tokenText(tokens, hover.col)} · ${hover.value.toFixed(3)}`
              : `${
                  step.name.startsWith('kq-')
                    ? 'Raw scores q·k before the mask and softmax — negative is allowed, and a query may score keys after it here; the next node is where causality is applied. '
                    : ''
                }Rows are queries, columns are the keys they attend to. ${m.cols} KV positions in use${
                  m.kvCropped
                    ? ' (the allocated cache is wider; its masked columns are dropped)'
                    : ''
                }.`}
          </p>
          <div className="stp-attn-top">
            <span className="stp-label">From {tokenText(tokens, position)}</span>
            {queryRow >= 0 && queryRow < m.rows ? (
              topKeys(m, queryRow, 5).map(([col, w]) => (
                <button
                  key={col}
                  className="stp-chip"
                  onClick={() => onPosition(col)}
                  title={`key position ${col}`}
                >
                  {tokenText(tokens, col)} <span className="stp-num">{w.toFixed(2)}</span>
                </button>
              ))
            ) : (
              <span className="llama-meta">this node did not compute that position</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function topKeys(m: RecordMatrix, row: number, k: number): [number, number][] {
  const out: [number, number][] = [];
  for (let c = 0; c < m.cols; c++) out.push([c, m.values[row * m.cols + c]]);
  return out.sort((a, b) => b[1] - a[1]).slice(0, k);
}

// ── residual ────────────────────────────────────────────────────────────────

function ResidualView(props: ViewProps) {
  const { traceId, step, tokens, position, onPosition } = props;
  const layer = step.layer ?? 0;
  const delta = useLoad<ResidualDelta>(`${traceId}:${layer}:${step.passIndex}:delta`, () =>
    getResidualDelta(traceId, layer, step.passIndex),
  );
  const lens = useLoad<LensGrid>(`${traceId}:${layer}:${step.passIndex}:lens`, () =>
    getLensGrid(traceId, { layers: [layer], k: 5, passIndex: step.passIndex }),
  );
  const d = delta.data;
  const row = lens.data?.cells[0] ?? [];
  const positions = lens.data?.positions ?? [];
  return (
    <div className="stp-view">
      <h4 className="stp-h">What block {layer} wrote</h4>
      {delta.error ? (
        <p className="llama-meta">{delta.error}</p>
      ) : !d ? (
        <p className="llama-meta">Reading…</p>
      ) : (
        <>
          <NormBars
            norms={d.relative}
            offset={d.positions[0] ?? 0}
            tokens={tokens}
            position={position}
            onPosition={onPosition}
            label="‖Δ‖/‖x‖"
          />
          <p className="stp-caption">
            Bar: the share of each token&rsquo;s residual vector this block rewrote. Turn at{' '}
            {tokenText(tokens, position)}:{' '}
            {(() => {
              const i = d.positions.indexOf(position);
              return i >= 0
                ? `cosine ${d.cosine[i].toFixed(3)} (${((Math.acos(Math.min(1, Math.max(-1, d.cosine[i]))) * 180) / Math.PI).toFixed(1)}°)`
                : 'not computed at this block';
            })()}
          </p>
        </>
      )}
      <h4 className="stp-h">What the stream says now</h4>
      {lens.error ? (
        <p className="llama-meta">Lens unavailable: {lens.error}</p>
      ) : !lens.data ? (
        <p className="llama-meta">Unembedding…</p>
      ) : (
        <div className="stp-lens">
          {lens.data.verified === 'false' && (
            <p className="llama-note">
              The lens could not reproduce this trace&rsquo;s own logits — read these as suspect.
            </p>
          )}
          {positions.map((pos, i) => {
            const cell = row[i];
            return (
              <button
                key={pos}
                className={`stp-lens-col${pos === position ? ' stp-bar-on' : ''}`}
                onClick={() => onPosition(pos)}
              >
                <span className="stp-bar-token">{tokenText(tokens, pos)}</span>
                {cell ? (
                  cell.texts.slice(0, 3).map((text, k) => (
                    <span key={k} className={`stp-lens-guess${k === 0 ? ' stp-lens-top' : ''}`}>
                      {JSON.stringify(text)}
                    </span>
                  ))
                ) : (
                  <span className="llama-meta">not computed</span>
                )}
              </button>
            );
          })}
        </div>
      )}
      <PlaneView {...props} />
    </div>
  );
}

// ── mixture of experts ──────────────────────────────────────────────────────

function ExpertView({ traceId, step, tokens, position }: ViewProps) {
  const routing = useLoad<ExpertRouting>(`${traceId}:${step.passIndex}:experts`, () =>
    getExperts(traceId, step.passIndex),
  );
  const r = routing.data;
  const atlas = useMemo(() => {
    if (!r?.moe) return null;
    const cols = Math.max(1, r.nExpert);
    const data: number[] = [];
    for (const layer of r.layers) for (let e = 0; e < cols; e++) data.push(layer.counts[e] ?? 0);
    return { data, rows: r.layers.length, cols };
  }, [r]);
  if (routing.error) return <p className="llama-note">{routing.error}</p>;
  if (!r) return <p className="llama-meta">Reading the router…</p>;
  if (!r.moe || !atlas)
    return (
      <p className="llama-meta">
        No routing in this trace — a dense model, or a capture set without the{' '}
        <code>ffn_moe_*</code> nodes.
      </p>
    );
  const here = r.layers.find((l) => l.layer === step.layer);
  const tokenRow = here ? position - (tokens.length - here.selections.length) : -1;
  return (
    <div className="stp-view">
      <h4 className="stp-h">
        Expert atlas · {r.layers.length} layers × {r.nExpert} experts
      </h4>
      <HeatCanvas
        data={atlas.data}
        rows={atlas.rows}
        cols={atlas.cols}
        label="Tokens routed to each expert, by layer"
        height={Math.min(260, 6 * atlas.rows + 40)}
      />
      {here && tokenRow >= 0 && tokenRow < here.selections.length && (
        <div className="stp-attn-top">
          <span className="stp-label">
            Block {here.layer} sent {tokenText(tokens, position)} to
          </span>
          {here.selections[tokenRow].map((e, k) => (
            <span key={e} className="stp-chip">
              expert {e}
              {here.weights ? (
                <span className="stp-num"> {here.weights[tokenRow][k].toFixed(2)}</span>
              ) : null}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── dispatch ────────────────────────────────────────────────────────────────

export interface ViewProps {
  traceId: string;
  step: Step;
  tokens: readonly TraceToken[];
  position: number;
  onPosition: (position: number) => void;
}

export function StageView(props: ViewProps) {
  const { step } = props;
  if (step.fidelity === 'summary') {
    return (
      <p className="llama-meta">
        <code>{step.name}</code> was stored as a summary — statistics instead of the tensor — so
        there are no values to draw. Its numbers are in Locals.
      </p>
    );
  }
  if (step.stage === 'moe' || step.name.startsWith('ffn_moe_')) return <ExpertView {...props} />;
  if (isAttentionScores(step.name)) return <AttentionView {...props} />;
  if (step.stage === 'residual' && step.layer !== null) return <ResidualView {...props} />;
  return <PlaneView {...props} />;
}
