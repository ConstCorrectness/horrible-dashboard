/**
 * NLA: a residual-stream vector put into words by one model, and checked by another.
 *
 * A Natural Language Autoencoder pair, run locally on llama.cpp
 * (`interp/nla_runner.py`): the base model is read at the pair's layer, the AV
 * writes an explanation of one position's vector (fed in as an embedding), and the
 * AR reconstructs the vector from that explanation. The reconstruction's cosine to
 * the original is the explanation's score, read against a floor measured in the
 * same run: a neutral sentence's reconstruction. Layer-K residuals share a common
 * direction, so any text lands ~0.4 from a vector; only the margin above that says
 * the explanation is about *this* one.
 *
 * Top to bottom: the pair and its files; what to read and which positions; the
 * token strip with each explained position underlined by its verdict; the
 * position card — the explanation, the score against its floor, other samples,
 * and an editor that scores a rewritten explanation against the same vector.
 */
import { useEffect, useMemo, type CSSProperties } from 'react';

import { RollingCounter } from '../../../viz/RollingCounter';
import { interpretabilityStore } from '../../interpretability/store';
import { askInterp } from '../ask';
import { A } from '../attention/styles';
import { refreshFeatureTraces, useFeatures } from '../features/state';
import { shownToken } from '../library/NeuronpediaCard';
import { S } from '../styles';
import { SuiteFrame } from '../SuiteFrame';
import type { ExplainedPosition, NlaPair, Readout, Sample, ScoreResult, Verdict } from './api';
import {
  currentPair,
  openReadout,
  parsePositions,
  refreshNla,
  run,
  scoreEdit,
  selectPosition,
  setEdit,
  setForm,
  useNla,
  type NlaSource,
} from './state';

export function NlaBody() {
  return (
    <SuiteFrame title="NLA">
      <NlaSection />
    </SuiteFrame>
  );
}

const VERDICT_COLOR: Record<Verdict, string> = {
  carried: 'var(--success)',
  partial: 'var(--warn)',
  drifted: 'var(--danger)',
  not_checked: 'var(--text-dim)',
};

const SOURCES: { id: NlaSource; label: string }[] = [
  { id: 'text', label: 'Text' },
  { id: 'conversation', label: 'Conversation' },
  { id: 'turn', label: 'Agent turn' },
  { id: 'trace', label: 'Trace' },
];

const fileName = (p: string) => p.split(/[\\/]/).pop() ?? p;

export function NlaSection() {
  const st = useNla();
  useEffect(() => {
    void refreshNla();
  }, []);
  const pair = currentPair(st);
  return (
    <div style={A.root}>
      <section style={{ ...A.ribbonWrap, paddingTop: 10 }} aria-label="NLA pair">
        <div style={T.row}>
          <div style={T.field}>
            <span style={S.heading}>Pair</span>
            <span style={T.pairName}>{pair?.label ?? 'none known'}</span>
          </div>
          {pair && <PairChip pair={pair} />}
          <span style={{ flex: 1 }} />
          {st.readouts.length > 0 && (
            <label style={T.field}>
              <span style={S.heading}>Readout</span>
              <select
                value={st.readout?.id ?? ''}
                onChange={(e) => void openReadout(e.target.value)}
                aria-label="Stored readout"
                style={{ minWidth: 240 }}
              >
                {st.readouts.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.id} · {r.source?.kind} · {r.positions} pos · cos{' '}
                    {r.meanCos?.toFixed(2) ?? '—'}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {pair && !pair.ready && <MissingFiles pair={pair} />}
      </section>
      {st.error && <div style={A.conflict}>{st.error}</div>}
      {pair?.ready && <ExplainPanel />}
      {st.readout ? (
        <ReadoutView r={st.readout} />
      ) : (
        !st.running && (
          <p style={A.emptyText}>
            The explainer (AV) is handed one position&apos;s layer-{pair?.layer ?? 20} residual in
            place of a token and writes what it means; the reconstructor (AR) reads that explanation
            and predicts the vector back. Their agreement is the score. Everything runs here on
            llama.cpp: about 20 s per explanation on the CPU, and the three models load one at a
            time.
          </p>
        )
      )}
    </div>
  );
}

function PairChip({ pair }: { pair: NlaPair }) {
  const color = pair.ready ? 'var(--success)' : 'var(--warn)';
  return (
    <div style={T.chip(color)} title={pair.source}>
      <span style={{ ...S.heading, color }}>{pair.ready ? 'ready' : 'files missing'}</span>
      <span style={S.mono}>
        L{pair.layer} · d {pair.nEmbd} · in-distribution FVE {pair.fveNrm}
      </span>
    </div>
  );
}

function MissingFiles({ pair }: { pair: NlaPair }) {
  const names: Record<string, string> = {
    base: `the base model (${pair.baseRepo}) as a GGUF`,
    av: 'the AV (explainer) GGUF',
    ar: 'the AR (reconstructor) GGUF with its value head',
  };
  return (
    <ul style={T.missing}>
      {(['base', 'av', 'ar'] as const).map((part) => (
        <li key={part} style={S.mono}>
          {part.toUpperCase()}:{' '}
          {pair.files[part].length ? fileName(pair.files[part][0]) : `not found — ${names[part]}`}
        </li>
      ))}
    </ul>
  );
}

// ── what to explain ──────────────────────────────────────────────────────────

function ExplainPanel() {
  const st = useNla();
  const f = st.form;
  const pair = currentPair(st);
  const features = useFeatures();
  const turns = useMemo(() => interpretabilityStore.getSnapshot().slice(-20).reverse(), []);
  useEffect(() => {
    if (f.source === 'trace' && !features.traces.length) void refreshFeatureTraces();
  }, [f.source, features.traces.length]);
  const baseName = pair?.files.base[0] ? fileName(pair.files.base[0]).replace(/\.gguf$/i, '') : '';
  const traces = features.traces.filter((t) => !t.modelName || t.modelName === baseName);
  const run_ = st.running;
  const n = parsePositions(f.positions).length || f.limit;
  return (
    <section style={A.ribbonWrap} aria-label="Explain">
      <div style={A.panelHead}>
        <span style={S.heading}>Explain</span>
        <span style={S.mono}>
          {pair?.label} · {n} position{n === 1 ? '' : 's'} × {f.samples} sample
          {f.samples === 1 ? '' : 's'}
        </span>
      </div>
      <div style={T.row}>
        <div role="tablist" style={T.tabs}>
          {SOURCES.map((s) => (
            <button
              key={s.id}
              type="button"
              role="tab"
              className="btn-mini"
              aria-selected={f.source === s.id}
              style={T.tab(f.source === s.id)}
              onClick={() => setForm({ source: s.id })}
            >
              {s.label}
            </button>
          ))}
        </div>
        {f.source === 'turn' && (
          <select
            value={f.turnId}
            onChange={(e) => setForm({ turnId: e.target.value })}
            aria-label="Captured agent turn"
            style={{ flex: 1, minWidth: 220 }}
          >
            <option value="">
              {turns.length ? 'pick a captured turn' : 'no captured turns yet'}
            </option>
            {turns.map((t) => (
              <option key={t.turnId} value={t.turnId}>
                {t.agentName || t.agentId} · {t.model} · {t.rounds.length} rounds ·{' '}
                {new Date(t.startedAt * 1000).toLocaleTimeString()}
              </option>
            ))}
          </select>
        )}
        {f.source === 'trace' && (
          <select
            value={f.traceId}
            onChange={(e) => setForm({ traceId: e.target.value })}
            aria-label="Trace of the base model"
            style={{ flex: 1, minWidth: 220 }}
          >
            <option value="">{traces.length ? 'pick a trace' : `no traces of ${baseName}`}</option>
            {traces.map((t) => (
              <option key={t.traceId} value={t.traceId}>
                {t.traceId} · {t.prompt.slice(0, 60)}
              </option>
            ))}
          </select>
        )}
      </div>
      {f.source === 'text' && (
        <textarea
          value={f.text}
          onChange={(e) => setForm({ text: e.target.value })}
          placeholder="Text to read — raw, no chat template."
          aria-label="Text to read"
          rows={3}
          style={T.textarea}
        />
      )}
      {f.source === 'conversation' && (
        <textarea
          value={f.conversation}
          onChange={(e) => setForm({ conversation: e.target.value })}
          placeholder={'system: …\nuser: …\nassistant: …'}
          aria-label="Conversation to read"
          rows={5}
          style={T.textarea}
        />
      )}
      <div style={T.row}>
        <label style={T.field}>
          <span style={S.heading}>Positions</span>
          <input
            type="text"
            value={f.positions}
            onChange={(e) => setForm({ positions: e.target.value })}
            placeholder={`last ${f.limit}`}
            aria-label="Positions to explain, comma separated, negative from the end"
            style={{ width: 120 }}
          />
        </label>
        <NumberField
          label="Last N"
          value={f.limit}
          min={1}
          max={16}
          onChange={(v) => setForm({ limit: v })}
        />
        <NumberField
          label="Samples"
          value={f.samples}
          min={1}
          max={5}
          onChange={(v) => setForm({ samples: v })}
        />
        <NumberField
          label="Max tokens"
          value={f.maxTokens}
          min={16}
          max={400}
          onChange={(v) => setForm({ maxTokens: v })}
        />
        <label style={T.field}>
          <span style={S.heading}>Temp</span>
          <input
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={f.temperature}
            onChange={(e) => setForm({ temperature: Math.max(0, Number(e.target.value) || 0) })}
            aria-label="Sampling temperature"
            style={{ width: 64 }}
          />
        </label>
        <span style={{ flex: 1 }} />
        <button type="button" disabled={!!run_} onClick={() => void run()}>
          {run_ ? 'Explaining…' : 'Explain'}
        </button>
      </div>
      {run_ && (
        <div style={{ display: 'grid', gap: 4 }}>
          <div style={S.mono}>
            {run_.phase}
            {run_.total ? ` · ${run_.done}/${run_.total}` : ''}
          </div>
          {run_.total > 0 && (
            <div style={T.progress}>
              <div style={{ ...T.progressFill, width: `${(100 * run_.done) / run_.total}%` }} />
            </div>
          )}
          {run_.latest && <div style={T.latest}>{run_.latest}</div>}
        </div>
      )}
    </section>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <label style={T.field}>
      <span style={S.heading}>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Math.min(max, Math.max(min, Number(e.target.value) || min)))}
        aria-label={label}
        style={{ width: 64 }}
      />
    </label>
  );
}

// ── the readout ──────────────────────────────────────────────────────────────

function ReadoutView({ r }: { r: Readout }) {
  const st = useNla();
  const sum = r.summary;
  const lift =
    sum.meanCos != null && sum.meanNullCos != null ? sum.meanCos - sum.meanNullCos : undefined;
  const p = r.positions.find((x) => x.position === st.position) ?? r.positions[0];
  return (
    <>
      <section style={A.ribbonWrap} aria-label="Readout">
        <div style={A.panelHead}>
          <span style={S.heading}>Readout</span>
          <span style={S.mono}>
            {r.source.kind}
            {r.source.traceId ? ` ${r.source.traceId}` : ''} · L{r.layer} · {r.tokens.length} tokens
            · {r.timing.total.toFixed(0)} s
            {r.avTokensPerSecond ? ` · AV ${r.avTokensPerSecond} tok/s` : ''}
          </span>
        </div>
        <div style={T.stats}>
          <Stat label="Mean cos" value={sum.meanCos} digits={2} />
          <Stat label="Above neutral" value={lift} digits={2} signed />
          <Stat label="Carried" value={sum.carried} suffix={` / ${sum.positions}`} />
          <Stat label="Flagged" value={sum.flagged} suffix={` / ${sum.positions}`} />
        </div>
        <TokenStrip r={r} />
      </section>
      {p && <PositionCard r={r} p={p} />}
    </>
  );
}

function Stat({
  label,
  value,
  digits = 0,
  signed,
  suffix = '',
}: {
  label: string;
  value: number | null | undefined;
  digits?: number;
  signed?: boolean;
  suffix?: string;
}) {
  return (
    <div style={T.stat}>
      <span style={S.heading}>{label}</span>
      <span style={T.statValue}>
        {value == null ? (
          '—'
        ) : (
          <RollingCounter
            value={value}
            format={(v) => `${signed && v > 0 ? '+' : ''}${v.toFixed(digits)}${suffix}`}
          />
        )}
      </span>
    </div>
  );
}

/** Every token; explained ones underlined by verdict, others pickable for the next run. */
function TokenStrip({ r }: { r: Readout }) {
  const st = useNla();
  const explained = new Map(r.positions.map((p) => [p.position, p]));
  const queued = new Set(
    parsePositions(st.form.positions).map((p) => (p < 0 ? r.tokens.length + p : p)),
  );
  const pick = (i: number) => {
    if (explained.has(i)) {
      selectPosition(i);
      return;
    }
    const next = queued.has(i) ? [...queued].filter((q) => q !== i) : [...queued, i];
    setForm({ positions: next.sort((a, b) => a - b).join(', ') });
  };
  return (
    <div
      style={T.strip}
      aria-label="Tokens; click an explained one to open it, another to queue it"
    >
      {r.tokens.map((tok, i) => {
        const p = explained.get(i);
        const on = p && p.position === st.position;
        return (
          <button
            key={i}
            type="button"
            className="nla-token"
            style={T.token(p ? VERDICT_COLOR[p.verdict] : null, Boolean(on), queued.has(i))}
            title={`${i} · ‖v‖ ${r.norms[i]?.toFixed(0) ?? '?'}${p ? ` · ${p.verdict} cos ${p.best?.cos ?? '—'}` : ' · click to queue for the next run'}`}
            onClick={() => pick(i)}
          >
            {shownToken(tok)}
          </button>
        );
      })}
    </div>
  );
}

function PositionCard({ r, p }: { r: Readout; p: ExplainedPosition }) {
  const st = useNla();
  const edit = st.edits[p.position];
  const best = p.best;
  const others = p.samples.filter((s) => s !== best && s.raw !== best?.raw);
  const color = VERDICT_COLOR[p.verdict];
  return (
    <section style={{ ...A.ribbonWrap, ...T.cardAccent(color) }} aria-label="Position">
      <div style={A.panelHead}>
        <span style={S.heading}>
          Position {p.position} · <span style={T.tokenName}>{shownToken(p.token)}</span>
        </span>
        <span style={S.mono}>
          ‖v‖ {p.norm.toFixed(0)}
          {p.flags.includes('early') && ' · early position: little context at this layer yet'}
          {p.flags.includes('high_norm') && ' · unusually high norm: decodes unreliably'}
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="btn-mini"
          onClick={() =>
            askInterp(
              `Explain the NLA readout at position ${p.position} ("${p.token.trim()}") of readout ${r.id}: what the explanation says, whether the score above the neutral floor supports it, and what the flags mean.`,
            )
          }
        >
          Ask
        </button>
      </div>
      <div style={T.cardGrid}>
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ ...S.heading, color }}>{p.verdict.replace('_', ' ')}</div>
          <p style={T.explanation}>
            {best?.explanation || '(no explanation)'}
            {best && !best.closed && <span style={S.mono}> … (cut short)</span>}
          </p>
          {others.length > 0 && (
            <details>
              <summary style={S.mono}>
                {others.length} other sample{others.length === 1 ? '' : 's'}
              </summary>
              {others.map((s, i) => (
                <div key={i} style={T.other}>
                  <span style={S.mono}>cos {s.cos?.toFixed(3) ?? '—'}</span>
                  <p style={T.explanation}>{s.explanation}</p>
                </div>
              ))}
            </details>
          )}
        </div>
        <div style={{ display: 'grid', gap: 10, alignContent: 'start' }}>
          {best && <ScoreBar s={best} />}
          <EditBox position={p.position} seed={best?.explanation ?? ''} edit={edit} />
        </div>
      </div>
    </section>
  );
}

/** cos on a 0–1 track, with the neutral floor and the 0.5 / 0.8 bands marked. */
function ScoreBar({ s }: { s: Pick<Sample, 'cos' | 'nullCos' | 'mse'> }) {
  const st = useNla();
  const good = st.defaults?.goodCos ?? 0.8;
  const weak = st.defaults?.weakCos ?? 0.5;
  const cos = Math.max(0, s.cos ?? 0);
  return (
    <div style={{ display: 'grid', gap: 4 }}>
      <div style={T.scoreRow}>
        <span style={S.heading}>Reconstruction</span>
        <span style={T.statValue}>
          <RollingCounter value={s.cos ?? 0} format={(v) => v.toFixed(3)} />
        </span>
      </div>
      <div style={T.track} role="img" aria-label={`cos ${s.cos}, neutral floor ${s.nullCos}`}>
        <div style={{ ...T.fill, width: `${cos * 100}%` }} />
        {[weak, good].map((x) => (
          <span key={x} style={{ ...T.tick, left: `${x * 100}%` }} />
        ))}
        {s.nullCos != null && (
          <span style={{ ...T.nullTick, left: `${Math.max(0, s.nullCos) * 100}%` }} />
        )}
      </div>
      <div style={S.mono}>
        neutral floor {s.nullCos?.toFixed(3) ?? '—'} · above it{' '}
        {s.cos != null && s.nullCos != null ? (s.cos - s.nullCos).toFixed(3) : '—'} · MSE{' '}
        {s.mse?.toFixed(3) ?? '—'}
      </div>
    </div>
  );
}

function EditBox({
  position,
  seed,
  edit,
}: {
  position: number;
  seed: string;
  edit: { text: string; running: boolean; result: ScoreResult | null } | undefined;
}) {
  const text = edit?.text ?? '';
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      <span style={S.heading}>Rewrite and re-score</span>
      <textarea
        value={text}
        onChange={(e) => setEdit(position, e.target.value)}
        placeholder="Write or edit an explanation; the AR scores it against the same vector."
        aria-label="Explanation to score"
        rows={4}
        style={T.textarea}
      />
      <div style={T.row}>
        <button
          type="button"
          className="btn-mini"
          disabled={!seed}
          onClick={() => setEdit(position, seed)}
        >
          Start from this one
        </button>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          disabled={!text.trim() || edit?.running}
          onClick={() => void scoreEdit(position)}
        >
          {edit?.running ? 'Scoring…' : 'Score'}
        </button>
      </div>
      {edit?.result && (
        <div style={{ ...T.chip(VERDICT_COLOR[edit.result.verdict]), gap: 6 }}>
          <span style={{ ...S.heading, color: VERDICT_COLOR[edit.result.verdict] }}>
            {edit.result.verdict.replace('_', ' ')}
          </span>
          <ScoreBar s={edit.result} />
        </div>
      )}
    </div>
  );
}

const T = {
  row: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 12,
    flexWrap: 'wrap',
  } satisfies CSSProperties,
  field: { display: 'grid', gap: 4 } satisfies CSSProperties,
  pairName: { ...S.mono, fontSize: 13, color: 'var(--text)' } satisfies CSSProperties,
  chip(color: string): CSSProperties {
    return {
      ...A.card,
      display: 'grid',
      gap: 2,
      borderTop: `2px solid ${color}`,
      padding: '6px 10px',
    };
  },
  missing: { margin: '8px 0 0', paddingLeft: 18, display: 'grid', gap: 2 } satisfies CSSProperties,
  textarea: {
    width: '100%',
    boxSizing: 'border-box',
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    resize: 'vertical',
  } satisfies CSSProperties,
  progress: { height: 3, background: 'var(--bg)', overflow: 'hidden' } satisfies CSSProperties,
  progressFill: {
    height: '100%',
    background: 'var(--accent)',
    transition: 'width 300ms ease',
  } satisfies CSSProperties,
  latest: {
    fontSize: 12,
    lineHeight: 1.5,
    color: 'var(--text-secondary)',
    borderLeft: '2px solid var(--border)',
    paddingLeft: 8,
    whiteSpace: 'pre-wrap',
  } satisfies CSSProperties,
  tabs: { display: 'flex', gap: 2 } satisfies CSSProperties,
  tab(on: boolean): CSSProperties {
    return {
      ...S.heading,
      fontSize: 10,
      border: 'none',
      borderBottom: `2px solid ${on ? 'var(--accent)' : 'transparent'}`,
      background: 'transparent',
      color: on ? 'var(--text)' : 'var(--text-secondary)',
      cursor: 'pointer',
      padding: '0 8px',
      height: 26,
    };
  },
  stats: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
    gap: 10,
    marginBottom: 10,
  } satisfies CSSProperties,
  stat: { ...A.card, display: 'grid', gap: 2 } satisfies CSSProperties,
  statValue: {
    fontFamily: 'var(--font-mono)',
    fontSize: 18,
    fontWeight: 700,
  } satisfies CSSProperties,
  strip: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 1,
    maxHeight: 220,
    overflowY: 'auto',
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
  } satisfies CSSProperties,
  token(verdict: string | null, on: boolean, queued: boolean): CSSProperties {
    return {
      fontFamily: 'inherit',
      fontSize: 'inherit',
      border: 'none',
      borderBottom: `2px solid ${verdict ?? (queued ? 'var(--accent)' : 'transparent')}`,
      background: on
        ? 'color-mix(in srgb, var(--accent) 18%, transparent)'
        : queued
          ? 'color-mix(in srgb, var(--accent) 8%, transparent)'
          : 'transparent',
      color: verdict || queued ? 'var(--text)' : 'var(--text-secondary)',
      padding: '0 2px',
      height: 20,
      cursor: 'pointer',
      whiteSpace: 'pre',
    };
  },
  cardAccent(color: string): CSSProperties {
    return { borderTop: `2px solid ${color}` };
  },
  tokenName: { fontFamily: 'var(--font-mono)', textTransform: 'none' } satisfies CSSProperties,
  cardGrid: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1.4fr) minmax(240px, 1fr)',
    gap: 16,
    alignItems: 'start',
  } satisfies CSSProperties,
  explanation: {
    margin: 0,
    fontSize: 13,
    lineHeight: 1.6,
    whiteSpace: 'pre-wrap',
  } satisfies CSSProperties,
  other: {
    display: 'grid',
    gap: 4,
    borderLeft: '2px solid var(--border)',
    paddingLeft: 8,
    marginTop: 8,
  } satisfies CSSProperties,
  scoreRow: {
    display: 'flex',
    alignItems: 'baseline',
    justifyContent: 'space-between',
  } satisfies CSSProperties,
  track: {
    position: 'relative',
    height: 8,
    background: 'var(--bg)',
    borderBottom: '1px solid var(--border)',
  } satisfies CSSProperties,
  fill: {
    position: 'absolute',
    inset: '0 auto 0 0',
    background: 'var(--accent)',
    transition: 'width 400ms ease',
  } satisfies CSSProperties,
  tick: {
    position: 'absolute',
    top: -2,
    bottom: -2,
    width: 1,
    background: 'var(--text-dim)',
  } satisfies CSSProperties,
  nullTick: {
    position: 'absolute',
    top: -4,
    bottom: -4,
    width: 2,
    marginLeft: -1,
    background: 'var(--text)',
  } satisfies CSSProperties,
};
