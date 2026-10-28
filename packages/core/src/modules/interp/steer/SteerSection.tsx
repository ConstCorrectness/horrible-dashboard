/**
 * Steer: add a feature's direction to the residual stream and read what changes.
 *
 * A feature (from the Features section, or typed) is pushed into the model at its
 * SAE's layer with a llama.cpp control vector, at a strength measured in the
 * feature's own peak — 1× is as strong as it ever fires on the prompt. The
 * default and steered generations stand side by side, greedy, so any difference
 * is the vector's. Above them, the evidence it took: did the feature itself move
 * the way it was pushed, and how far did the first prediction shift (the tokens
 * that gained and lost the most).
 *
 * Both runs are traces — the steered one a fork of the default, with the push in
 * its provenance — so either opens in Features, Lens or Stepper like any other.
 */
import { useEffect, type CSSProperties } from 'react';

import { revealSection } from '../../../layout/controller';
import { RollingCounter } from '../../../viz/RollingCounter';
import { askInterp } from '../ask';
import { A } from '../attention/styles';
import {
  openTrace as openFeatureTrace,
  refreshFeatureTraces,
  useFeatures,
} from '../features/state';
import { SUITE_SECTIONS, SUITE_VIEW } from '../ids';
import { shownToken } from '../library/NeuronpediaCard';
import { S } from '../styles';
import { SuiteFrame } from '../SuiteFrame';
import type { Mover, SteerResult } from './api';
import { run, setDraft, useSteer } from './state';

export function SteerBody() {
  return (
    <SuiteFrame title="Steer">
      <SteerSection />
    </SuiteFrame>
  );
}

/** Strengths beyond this broke Gemma 3 1B into punctuation (measured); allowed, marked. */
const COHERENT = 1.5;

export function SteerSection() {
  const st = useSteer();
  const features = useFeatures();

  useEffect(() => {
    if (!features.traces.length) void refreshFeatureTraces();
  }, [features.traces.length]);

  // Nothing chosen yet: offer the Features section's current selection.
  useEffect(() => {
    if (st.draft.feature === null && features.result && features.feature !== null) {
      setDraft({
        artifactId: features.result.artifactId,
        feature: features.feature,
        label: features.cards[features.feature]?.explanations?.[0]?.text ?? '',
        traceId: features.result.traceId,
        prompt: features.traces.find((t) => t.traceId === features.result?.traceId)?.prompt ?? '',
      });
    }
  }, [st.draft.feature, features.result, features.feature, features.cards, features.traces]);

  return (
    <div style={A.root}>
      <Controls />
      {st.error && <div style={A.conflict}>{st.error}</div>}
      {st.result ? <Result r={st.result} /> : !st.running && <Intro />}
      {st.history.length > 1 && <History />}
    </div>
  );
}

function Intro() {
  return (
    <p style={A.emptyText}>
      Pick a feature in Features and press <em>Steer with this</em>, or set one here. The default
      and steered generations appear side by side, with whether the feature actually moved and how
      far the next-token prediction shifted.
    </p>
  );
}

// ── controls ─────────────────────────────────────────────────────────────────

function Controls() {
  const st = useSteer();
  const features = useFeatures();
  const d = st.draft;
  const ready = Boolean(d.artifactId && d.feature !== null && d.traceId && d.prompt.trim());
  const sae = features.saes.find((s) => s.id === d.artifactId);
  return (
    <section style={{ ...A.ribbonWrap, borderTopColor: 'var(--accent)', paddingTop: 10 }}>
      <div style={T.controls}>
        <label style={T.field}>
          <span style={S.heading}>Feature</span>
          <input
            type="number"
            value={d.feature ?? ''}
            min={0}
            onChange={(e) =>
              setDraft({
                feature: e.target.value === '' ? null : Number(e.target.value),
                label: '',
              })
            }
            aria-label="Feature index to steer with"
            style={{ width: 96 }}
          />
        </label>
        <div style={T.featureMeta}>
          <span style={T.label}>{d.label || (d.feature !== null ? `F${d.feature}` : '—')}</span>
          <span style={S.mono}>
            {sae
              ? `${sae.choice.split('/').pop()} · layer ${sae.facts.layer ?? '?'}`
              : d.artifactId}
          </span>
        </div>
        <label style={T.field}>
          <span style={S.heading}>
            Strength ×{d.strength.toFixed(2)}
            {Math.abs(d.strength) > COHERENT && <span style={T.warn}> · past coherent</span>}
          </span>
          <input
            type="range"
            min={-3}
            max={3}
            step={0.25}
            value={d.strength}
            onChange={(e) => setDraft({ strength: Number(e.target.value) })}
            aria-label="Steering strength, in units of the feature's peak"
            style={{ width: 220 }}
          />
        </label>
        <label style={T.field}>
          <span style={S.heading}>Tokens</span>
          <input
            type="number"
            min={1}
            max={128}
            value={d.maxTokens}
            onChange={(e) => setDraft({ maxTokens: Math.max(1, Number(e.target.value) || 1) })}
            aria-label="Tokens to generate"
            style={{ width: 72 }}
          />
        </label>
      </div>
      <div style={T.promptRow}>
        <input
          type="text"
          value={d.prompt}
          onChange={(e) => setDraft({ prompt: e.target.value })}
          aria-label="Prompt to steer"
          placeholder="A prompt — raw text, no chat template"
          style={{ flex: 1, minWidth: 200 }}
        />
        <button type="button" disabled={!ready || !!st.running} onClick={() => void run()}>
          {st.running ? 'Steering…' : 'Steer'}
        </button>
      </div>
      <div style={S.mono}>
        {st.running
          ? `${st.running.phase === 'default' ? 'default run' : 'steered run'}: ${st.running.status}`
          : d.traceId
            ? `on the model of trace ${d.traceId} · the default run is reused while prompt, model and length stay the same`
            : 'no trace chosen — pick one in Features'}
      </div>
    </section>
  );
}

// ── the result ───────────────────────────────────────────────────────────────

const ARRIVED: Record<string, { text: string; color: string }> = {
  arrived: { text: 'arrived', color: 'var(--success)' },
  did_not_arrive: { text: 'did not arrive', color: 'var(--danger)' },
  nothing_to_suppress: { text: 'nothing to suppress', color: 'var(--text-dim)' },
  no_push: { text: 'no push', color: 'var(--text-dim)' },
};

function Result({ r }: { r: SteerResult }) {
  const a = ARRIVED[r.arrived.state] ?? ARRIVED.no_push;
  return (
    <>
      <div style={T.evidence}>
        <section style={T.chipPanel(a.color)} aria-label="Did the feature move">
          <span style={{ ...S.heading, color: a.color }}>{a.text}</span>
          <div style={T.big}>
            <RollingCounter value={r.arrived.defaultMean} format={(v) => v.toFixed(0)} />
            {' → '}
            <RollingCounter value={r.arrived.steeredMean} format={(v) => v.toFixed(0)} />
          </div>
          <span style={S.mono}>
            F{r.feature}&apos;s mean on the prompt (BOS out) · active on{' '}
            {Math.round(r.arrived.defaultActiveShare * 100)}% →{' '}
            {Math.round(r.arrived.steeredActiveShare * 100)}% of tokens
          </span>
          <span style={S.mono}>
            ×{r.strength} of peak {r.unit.toFixed(0)} = scale {r.scale.toFixed(0)} at L{r.layer}
          </span>
        </section>
        {r.nextToken && <NextToken nt={r.nextToken} />}
      </div>
      <div style={T.columns}>
        <Generation
          title="Default"
          text={r.default.tokens}
          diverges={r.diverges}
          traceId={r.defaultTraceId}
        />
        <Generation
          title={`Steered ×${r.strength}`}
          text={r.steered.tokens}
          diverges={r.diverges}
          traceId={r.steeredTraceId}
          accent
        />
      </div>
      <div style={A.actions}>
        <button
          type="button"
          onClick={() =>
            askInterp(
              `Steer section: F${r.feature} at ×${r.strength}, default trace ${r.defaultTraceId}, steered trace ${r.steeredTraceId}`,
            )
          }
        >
          Ask what changed
        </button>
      </div>
    </>
  );
}

function NextToken({ nt }: { nt: NonNullable<SteerResult['nextToken']> }) {
  const row = (m: Mover, sign: 1 | -1) => (
    <li key={m.id} style={T.mover}>
      <span style={T.moverToken}>{JSON.stringify(shownToken(m.token))}</span>
      <span style={S.mono}>
        {(m.default * 100).toFixed(1)}% → {(m.steered * 100).toFixed(1)}%
      </span>
      <span style={{ ...S.mono, color: sign > 0 ? 'var(--success)' : 'var(--danger)' }}>
        {m.deltaLogProb > 0 ? '+' : ''}
        {m.deltaLogProb.toFixed(2)}
      </span>
    </li>
  );
  return (
    <section style={T.chipPanel('var(--border)')} aria-label="First prediction">
      <span style={S.heading}>First prediction</span>
      <div style={T.big}>
        {JSON.stringify(shownToken(nt.defaultTop.token))} →{' '}
        {JSON.stringify(shownToken(nt.steeredTop.token))}
      </div>
      <span style={S.mono}>
        top: {(nt.defaultTop.default * 100).toFixed(1)}% →{' '}
        {(nt.steeredTop.steered * 100).toFixed(1)}% · KL {nt.kl.toFixed(3)} nats
      </span>
      <div style={T.movers}>
        <ol style={T.moverList} aria-label="Gained">
          {nt.gained.slice(0, 5).map((m) => row(m, 1))}
        </ol>
        <ol style={T.moverList} aria-label="Lost">
          {nt.lost.slice(0, 5).map((m) => row(m, -1))}
        </ol>
      </div>
    </section>
  );
}

function Generation({
  title,
  text,
  diverges,
  traceId,
  accent,
}: {
  title: string;
  text: string[];
  diverges: number | null;
  traceId: string;
  accent?: boolean;
}) {
  return (
    <section
      style={{ ...A.ribbonWrap, borderTopColor: accent ? 'var(--accent)' : 'var(--border)' }}
      aria-label={title}
    >
      <div style={A.panelHead}>
        <span style={S.heading}>{title}</span>
        <button
          type="button"
          className="btn-mini"
          title="Read this run's features"
          onClick={() => {
            void openFeatureTrace(traceId);
            void revealSection(SUITE_SECTIONS.features, SUITE_VIEW);
          }}
        >
          Features
        </button>
      </div>
      <p style={T.text}>
        {text.map((t, i) => (
          <span key={i} style={diverges !== null && i >= diverges ? T.after : undefined}>
            {shownToken(t)}
          </span>
        ))}
      </p>
      <span style={S.mono}>{traceId}</span>
    </section>
  );
}

function History() {
  const st = useSteer();
  return (
    <section style={A.ribbonWrap} aria-label="Earlier runs">
      <div style={A.panelHead}>
        <span style={S.heading}>Runs</span>
        <span style={S.mono}>newest first</span>
      </div>
      <ol style={T.history}>
        {st.history.map((h, i) => (
          <li key={h.steeredTraceId} style={{ ...T.historyRow, ...S.stagger(i) }}>
            <span style={T.moverToken}>
              F{h.feature} ×{h.strength}
            </span>
            <span style={{ ...S.mono, color: (ARRIVED[h.arrived.state] ?? ARRIVED.no_push).color }}>
              {(ARRIVED[h.arrived.state] ?? ARRIVED.no_push).text}
            </span>
            <span style={T.historyText}>{h.steered.text.replace(/\s+/g, ' ').slice(0, 90)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

// ── styles local to this section ─────────────────────────────────────────────

const T = {
  controls: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 16,
    flexWrap: 'wrap',
  } satisfies CSSProperties,
  field: { display: 'grid', gap: 4 } satisfies CSSProperties,
  featureMeta: { display: 'grid', gap: 2, minWidth: 160, flex: 1 } satisfies CSSProperties,
  label: { fontSize: 13, fontWeight: 600 } satisfies CSSProperties,
  warn: { color: 'var(--warn)' } satisfies CSSProperties,
  promptRow: {
    display: 'flex',
    gap: 8,
    alignItems: 'center',
    flexWrap: 'wrap',
  } satisfies CSSProperties,
  evidence: {
    display: 'grid',
    gridTemplateColumns: 'minmax(220px, 1fr) minmax(0, 1.6fr)',
    gap: 14,
    alignItems: 'start',
  } satisfies CSSProperties,
  chipPanel(color: string): CSSProperties {
    return {
      ...A.card,
      display: 'grid',
      gap: 6,
      borderTop: `2px solid ${color}`,
    };
  },
  big: { fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 700 } satisfies CSSProperties,
  movers: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 } satisfies CSSProperties,
  moverList: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'grid',
    gap: 2,
  } satisfies CSSProperties,
  mover: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) auto 48px',
    gap: 8,
    alignItems: 'baseline',
  } satisfies CSSProperties,
  moverToken: {
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    fontWeight: 700,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'pre',
  } satisfies CSSProperties,
  columns: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
    gap: 14,
    alignItems: 'start',
  } satisfies CSSProperties,
  text: {
    margin: 0,
    fontFamily: 'var(--font-mono)',
    fontSize: 12.5,
    lineHeight: 1.6,
    whiteSpace: 'pre-wrap',
    minHeight: 80,
  } satisfies CSSProperties,
  after: {
    background: 'color-mix(in srgb, var(--accent) 16%, transparent)',
  } satisfies CSSProperties,
  history: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'grid',
    gap: 3,
  } satisfies CSSProperties,
  historyRow: {
    display: 'grid',
    gridTemplateColumns: '110px 130px minmax(0, 1fr)',
    gap: 10,
    alignItems: 'baseline',
  } satisfies CSSProperties,
  historyText: {
    fontFamily: 'var(--font-mono)',
    fontSize: 11.5,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,
};
