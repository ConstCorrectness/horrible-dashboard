/**
 * Features: what an SAE reads in a trace — Gemma Scope 2 and friends, on the
 * residuals a llama.cpp trace already holds.
 *
 * Top to bottom: the trace and SAE, with the reconstruction self-check as a chip
 * (verified / drifted / not checked, with FVU and L0 beside the L0 the SAE was
 * trained to); the token strip, shaded by the selected feature; the features at
 * the selected token beside the trace's strongest ones; and the selected
 * feature's card — its activation across the prompt, Neuronpedia's label and
 * examples, and a label check that traces Neuronpedia's own example through this
 * GGUF to see whether the label still means what it says here.
 */
import { useEffect, type CSSProperties } from 'react';

import { useModelLocus } from '../../../model-locus';
import { RollingCounter } from '../../../viz/RollingCounter';
import { askInterp } from '../ask';
import { A } from '../attention/styles';
import { revealSection } from '../../../layout/controller';
import { SUITE_SECTIONS, SUITE_VIEW } from '../ids';
import { NeuronpediaCard, shownToken } from '../library/NeuronpediaCard';
import { S } from '../styles';
import { steerWith } from '../steer/state';
import { SuiteFrame } from '../SuiteFrame';
import type { FeatureEncoding, LabelCheck } from './api';
import {
  checkKey,
  chooseSae,
  labelOf,
  openTrace,
  refreshFeatureTraces,
  runLabelCheck,
  selectFeature,
  selectPosition,
  useFeatures,
} from './state';

export function FeaturesBody() {
  return (
    <SuiteFrame title="Features">
      <FeaturesSection />
    </SuiteFrame>
  );
}

export function FeaturesSection() {
  const st = useFeatures();
  const locus = useModelLocus();
  const r = st.result;

  useEffect(() => {
    void refreshFeatureTraces();
  }, []);

  // Another tool moved the locus on this trace: follow it.
  useEffect(() => {
    if (
      r &&
      locus.source !== 'features' &&
      locus.traceId === r.traceId &&
      typeof locus.position === 'number' &&
      locus.position !== st.position
    ) {
      selectPosition(locus.position, false);
    }
  }, [locus.position, locus.traceId, locus.source, r, st.position]);

  return (
    <div style={A.root}>
      <Toolbar />
      {st.error && <div style={A.conflict}>{st.error}</div>}
      {!r ? (
        <Empty />
      ) : (
        <>
          <TokenStrip r={r} />
          <div style={F.columns}>
            <AtToken r={r} />
            <TraceTop r={r} />
          </div>
          <FeatureCardPanel r={r} />
        </>
      )}
    </div>
  );
}

function Empty() {
  const st = useFeatures();
  if (st.loading) return <p style={A.emptyText}>Encoding…</p>;
  const anyFits = st.traces.some((t) => t.fits.length > 0);
  return (
    <div style={A.emptyText}>
      {anyFits ? (
        'Pick a trace.'
      ) : (
        <>
          No trace has an SAE that fits it. An SAE reads one model at one layer: get one in the{' '}
          <button
            type="button"
            className="btn-mini"
            onClick={() => void revealSection(SUITE_SECTIONS.library, SUITE_VIEW)}
          >
            Library
          </button>{' '}
          (Gemma Scope 2 for Gemma 3 1B IT), then record a trace of that model in Traces.
        </>
      )}
    </div>
  );
}

// ── toolbar: trace, SAE, and the self-check ──────────────────────────────────

function Toolbar() {
  const st = useFeatures();
  const r = st.result;
  return (
    <div style={F.toolbar}>
      <label style={F.field}>
        <span style={S.heading}>Trace</span>
        <select
          value={st.traceId ?? ''}
          onChange={(e) => void openTrace(e.target.value)}
          aria-label="Trace to read features from"
        >
          {st.traces.length === 0 && <option value="">no traces yet</option>}
          {st.traces.map((t) => (
            <option key={t.traceId} value={t.traceId}>
              {t.fits.length ? '' : '(no SAE) '}
              {t.modelName} · {t.prompt.replace(/\s+/g, ' ').slice(0, 40)}
            </option>
          ))}
        </select>
      </label>
      <label style={F.field}>
        <span style={S.heading}>SAE</span>
        <select
          value={st.artifactId ?? ''}
          onChange={(e) => void chooseSae(e.target.value)}
          aria-label="SAE to encode with"
          disabled={!st.saes.length}
        >
          {st.saes.length === 0 && <option value="">none installed</option>}
          {st.saes.map((s) => (
            <option key={s.id} value={s.id} disabled={s.compat.state === 'incompatible'}>
              {s.choice.split('/').pop()} · {s.compat.state}
            </option>
          ))}
        </select>
      </label>
      {r && <CheckChip r={r} />}
    </div>
  );
}

const STATE_COLOR: Record<string, string> = {
  verified: 'var(--success)',
  drifted: 'var(--warn)',
  failed: 'var(--danger)',
  not_checked: 'var(--text-dim)',
  running: 'var(--text-dim)',
};

function CheckChip({ r }: { r: FeatureEncoding }) {
  const c = r.check;
  const color = STATE_COLOR[c.state];
  const title =
    c.state === 'not_checked'
      ? c.reason
      : `How much of this trace's layer-${r.layer} residual the SAE reconstructs ` +
        `(position 0, the BOS sink, left out). Verified at FVU ≤ ${c.threshold}.`;
  return (
    <div style={F.chip(color)} title={title}>
      <span style={F.chipState(color)}>{c.state.replace('_', ' ')}</span>
      {c.state !== 'not_checked' && (
        <span style={S.mono}>
          FVU {c.fvu?.toFixed(3)} · cos {c.cosine?.toFixed(3)} · L0 {c.l0 ?? '?'}
          {c.expectedL0 ? ` / ${c.expectedL0} trained` : ''}
        </span>
      )}
      <span style={S.mono}>
        L{r.layer} · {r.width.toLocaleString()} · {r.arch} · {r.fidelity ?? '?'} trace
      </span>
      {r.compat.reasons[0] && <span style={S.mono}>· {r.compat.reasons[0]}</span>}
    </div>
  );
}

// ── the token strip ──────────────────────────────────────────────────────────

function TokenStrip({ r }: { r: FeatureEncoding }) {
  const st = useFeatures();
  const values = st.values && st.values.index === st.feature ? st.values : null;
  const top = values ? Math.max(1e-9, ...values.values) : 1;
  const valueAt = (p: number) =>
    values ? (values.values[p - values.firstPosition] ?? null) : null;
  return (
    <section style={{ ...A.ribbonWrap, paddingTop: 10 }} aria-label="Tokens">
      <div style={A.panelHead}>
        <span style={S.heading}>Tokens</span>
        <span style={S.mono}>
          {values ? `shaded by F${st.feature}` : 'pick a feature to shade by it'} · click a token
          for its features
        </span>
      </div>
      <div style={F.strip}>
        {r.tokens.map((t) => {
          const v = valueAt(t.position);
          const on = st.position === t.position;
          return (
            <button
              key={t.position}
              type="button"
              className="interp-col"
              style={F.token(v === null ? 0 : v / top, on)}
              title={`#${t.position}${v !== null ? ` · F${st.feature} ${v.toFixed(1)}` : ''}`}
              onClick={() => selectPosition(t.position)}
            >
              {t.position === 0 && !t.text.trim() ? '⟨bos⟩' : shownToken(t.text) || '·'}
            </button>
          );
        })}
      </div>
    </section>
  );
}

// ── features at the token, and across the trace ──────────────────────────────

function FeatureRow({
  index,
  value,
  max,
  note,
  rank,
}: {
  index: number;
  value: number;
  max: number;
  note?: string;
  rank: number;
}) {
  const st = useFeatures();
  const label = labelOf(st.cards, index);
  const on = st.feature === index;
  const dim = !label;
  return (
    <li style={S.stagger(rank)}>
      <button
        type="button"
        className="interp-col"
        style={F.row(on)}
        onClick={() => void selectFeature(index)}
      >
        <span style={F.featureId}>F{index}</span>
        <span style={dim ? { ...F.label, color: 'var(--text-dim)' } : F.label}>
          {label ??
            (!st.result?.neuronpedia
              ? 'no labels for this SAE'
              : st.cards[index]
                ? 'unlabelled on Neuronpedia'
                : '…')}
        </span>
        {note && <span style={F.note}>{note}</span>}
        <span style={F.bar}>
          <span style={{ ...F.fill, width: `${Math.round((value / max) * 100)}%` }} />
        </span>
        <span style={F.value}>
          <RollingCounter value={value} format={(v) => v.toFixed(1)} />
        </span>
      </button>
    </li>
  );
}

function AtToken({ r }: { r: FeatureEncoding }) {
  const st = useFeatures();
  const row = r.perToken.find((p) => p.position === st.position);
  const tok = r.tokens.find((t) => t.position === st.position);
  const max = Math.max(1e-9, ...(row?.top.map((f) => f.act) ?? [1]));
  return (
    <section
      style={{ ...A.ribbonWrap, borderTopColor: 'var(--accent)' }}
      aria-label="At this token"
    >
      <div style={A.panelHead}>
        <span style={S.heading}>At this token</span>
        <span style={S.mono}>
          {tok ? `#${tok.position} "${shownToken(tok.text)}"` : 'none selected'}
          {row ? ` · ${row.active} active` : ''}
        </span>
      </div>
      {!row ? (
        <p style={A.emptyText}>
          {st.position === null
            ? 'Click a token.'
            : 'This position was not computed at this layer (llama.cpp prunes the last layers to the final token).'}
        </p>
      ) : (
        <ol style={F.list}>
          {row.top.map((f, i) => (
            <FeatureRow key={f.index} index={f.index} value={f.act} max={max} rank={i} />
          ))}
        </ol>
      )}
    </section>
  );
}

function TraceTop({ r }: { r: FeatureEncoding }) {
  const top = r.top.slice(0, 12);
  const max = Math.max(1e-9, ...top.map((f) => f.peak));
  const tok = (p: number) => shownToken(r.tokens.find((t) => t.position === p)?.text ?? '');
  return (
    <section style={A.ribbonWrap} aria-label="Strongest in this trace">
      <div style={A.panelHead}>
        <span style={S.heading}>This trace</span>
        <span style={S.mono}>by peak · BOS left out</span>
      </div>
      <ol style={F.list}>
        {top.map((f, i) => (
          <FeatureRow
            key={f.index}
            index={f.index}
            value={f.peak}
            max={max}
            rank={i}
            note={f.firstToken ? 'first token' : `"${tok(f.position)}"`}
          />
        ))}
      </ol>
    </section>
  );
}

// ── the selected feature ─────────────────────────────────────────────────────

function FeatureCardPanel({ r }: { r: FeatureEncoding }) {
  const st = useFeatures();
  const index = st.feature;
  if (index === null) return null;
  const card = st.cards[index];
  const check = st.artifactId ? st.checks[checkKey(st.artifactId, index)] : undefined;
  const values = st.values?.index === index ? st.values : null;
  const top = r.top.find((f) => f.index === index);
  return (
    <section style={F.cardPanel} aria-label={`Feature ${index}`}>
      <div style={F.cardHead}>
        <span style={A.cardLayer}>F{index}</span>
        <span style={F.label}>{labelOf(st.cards, index) ?? ''}</span>
        {top?.firstToken && (
          <span
            style={F.flag}
            title="Peaks on the first token after BOS. Neuronpedia's examples never show that position, so its label describes the feature elsewhere."
          >
            first token
          </span>
        )}
      </div>

      {values && <Activations r={r} values={values.values} first={values.firstPosition} />}

      <div style={A.actions}>
        <button
          type="button"
          disabled={!r.neuronpedia || check === 'running'}
          title={
            r.neuronpedia
              ? "Trace Neuronpedia's top example for this feature through this GGUF and compare activations. Sends nothing of yours."
              : 'Neuronpedia hosts no labels for this SAE'
          }
          onClick={() => void runLabelCheck(index)}
        >
          {check === 'running' ? 'Checking…' : 'Check the label'}
        </button>
        <button
          type="button"
          onClick={() => askInterp(`Features section, feature F${index} at layer ${r.layer}`)}
        >
          Ask what it does
        </button>
        <button
          type="button"
          title="Add this feature's direction to the model at its layer, in the Steer section"
          onClick={() => {
            steerWith({
              artifactId: r.artifactId,
              feature: index,
              label: labelOf(st.cards, index),
              traceId: r.traceId,
              prompt: st.traces.find((t) => t.traceId === r.traceId)?.prompt ?? '',
            });
            void revealSection(SUITE_SECTIONS.steer, SUITE_VIEW);
          }}
        >
          Steer with this
        </button>
      </div>
      {check && check !== 'running' && <LabelVerdict check={check} />}

      {r.neuronpedia ? (
        card ? (
          <NeuronpediaCard card={card} examples={3} />
        ) : (
          <p style={A.emptyText}>Loading Neuronpedia&apos;s card…</p>
        )
      ) : (
        <p style={A.emptyText}>
          Neuronpedia hosts no labels for this dictionary — only the medium-L0 Gemma Scope SAEs have
          pages there.
        </p>
      )}
    </section>
  );
}

function LabelVerdict({ check }: { check: LabelCheck }) {
  const color = STATE_COLOR[check.state] ?? 'var(--text-dim)';
  return (
    <div style={F.verdict(color)}>
      <span style={F.chipState(color)}>label {check.state.replace('_', ' ')}</span>
      {check.expected !== undefined && check.ours !== undefined ? (
        <span style={S.mono}>
          Neuronpedia (bf16) {check.expected.toFixed(1)} · this GGUF {check.ours.toFixed(1)}
          {check.ratio != null ? ` · ×${check.ratio.toFixed(2)}` : ''}
          {check.token ? ` on "${check.token}"` : ''}
        </span>
      ) : (
        <span style={S.mono}>{check.reason}</span>
      )}
    </div>
  );
}

function Activations({
  r,
  values,
  first,
}: {
  r: FeatureEncoding;
  values: number[];
  first: number;
}) {
  const max = Math.max(1e-9, ...values);
  return (
    <div style={F.acts} aria-label="Activation at every token">
      {values.map((v, i) => {
        const position = first + i;
        const t = r.tokens.find((x) => x.position === position);
        return (
          <button
            key={position}
            type="button"
            className="interp-col"
            style={F.actCol}
            title={`#${position} "${shownToken(t?.text ?? '')}" · ${v.toFixed(1)}`}
            onClick={() => selectPosition(position)}
          >
            <span style={{ ...F.actBar, height: `${Math.round((v / max) * 100)}%` }} />
          </button>
        );
      })}
    </div>
  );
}

// ── styles local to this section ─────────────────────────────────────────────

const F = {
  toolbar: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 12,
    flexWrap: 'wrap',
  } satisfies CSSProperties,
  field: { display: 'grid', gap: 4, minWidth: 0, maxWidth: 360 } satisfies CSSProperties,
  chip(color: string): CSSProperties {
    return {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      flexWrap: 'wrap',
      minHeight: 30,
      padding: '0 10px',
      borderLeft: `2px solid ${color}`,
      background: 'var(--bg-secondary)',
    };
  },
  chipState(color: string): CSSProperties {
    return { ...S.heading, fontSize: 10, color };
  },
  verdict(color: string): CSSProperties {
    return {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      flexWrap: 'wrap',
      padding: '4px 0 4px 8px',
      borderLeft: `2px solid ${color}`,
    };
  },
  strip: { display: 'flex', flexWrap: 'wrap', gap: 2 } satisfies CSSProperties,
  token(share: number, on: boolean): CSSProperties {
    return {
      height: 24,
      padding: '0 4px',
      border: 'none',
      borderRadius: 0,
      borderBottom: `2px solid ${on ? 'var(--accent)' : 'transparent'}`,
      background:
        share > 0
          ? `color-mix(in srgb, var(--accent) ${Math.round(Math.min(1, share) * 75)}%, var(--bg-primary))`
          : 'var(--bg-primary)',
      color: 'var(--text-primary)',
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      whiteSpace: 'pre',
      cursor: 'pointer',
    };
  },
  columns: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.25fr)',
    gap: 14,
    alignItems: 'start',
  } satisfies CSSProperties,
  list: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'grid',
    gap: 2,
  } satisfies CSSProperties,
  row(on: boolean): CSSProperties {
    return {
      display: 'grid',
      gridTemplateColumns: '64px minmax(0, 1fr) auto 72px 52px',
      alignItems: 'center',
      gap: 8,
      width: '100%',
      height: 26,
      padding: '0 8px',
      border: 'none',
      borderLeft: `2px solid ${on ? 'var(--accent)' : 'transparent'}`,
      borderRadius: 0,
      background: on ? 'color-mix(in srgb, var(--accent) 10%, transparent)' : 'transparent',
      color: 'var(--text-primary)',
      cursor: 'pointer',
      textAlign: 'left',
    };
  },
  featureId: {
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    fontWeight: 700,
  } satisfies CSSProperties,
  label: {
    fontSize: 12,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    minWidth: 0,
  } satisfies CSSProperties,
  note: { ...S.mono, fontSize: 10, whiteSpace: 'pre' } satisfies CSSProperties,
  bar: { height: 5, background: 'var(--bg-primary)', position: 'relative' } satisfies CSSProperties,
  fill: {
    position: 'absolute',
    inset: '0 auto 0 0',
    background: 'var(--accent)',
    transition: 'width 200ms ease',
  } satisfies CSSProperties,
  value: { ...S.mono, textAlign: 'right', color: 'var(--text-primary)' } satisfies CSSProperties,
  cardPanel: {
    ...A.card,
    display: 'grid',
    gap: 10,
  } satisfies CSSProperties,
  cardHead: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 10,
    minWidth: 0,
  } satisfies CSSProperties,
  flag: {
    ...S.mono,
    fontSize: 10,
    textTransform: 'uppercase',
    letterSpacing: '0.1em',
    color: 'var(--warn)',
    borderLeft: '2px solid var(--warn)',
    paddingLeft: 6,
  } satisfies CSSProperties,
  acts: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 1,
    height: 56,
    background: 'var(--bg-primary)',
    padding: '4px 4px 0',
  } satisfies CSSProperties,
  actCol: {
    flex: '1 1 0',
    minWidth: 2,
    height: '100%',
    display: 'flex',
    alignItems: 'flex-end',
    padding: 0,
    border: 'none',
    borderRadius: 0,
    background: 'transparent',
    cursor: 'pointer',
  } satisfies CSSProperties,
  actBar: {
    display: 'block',
    width: '100%',
    background: 'var(--accent)',
    transition: 'height 160ms ease',
  } satisfies CSSProperties,
};
