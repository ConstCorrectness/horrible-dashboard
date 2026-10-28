/**
 * Compare lenses: one position of one trace, read through every lens installed for
 * its model — the logit lens and fitted Jacobian / J++ lenses — side by side.
 *
 * The two answer different questions. The logit lens reads what the model would
 * *say* if it stopped at a layer; a Jacobian lens transports the residual to the
 * final layer first, and reads what the model is *holding* — concepts it has not
 * yet decided to say. On "Mount Fuji is in a country whose national language is",
 * Gemma 3 1B's logit lens reads noise until layer 16; the J-lens reads "country",
 * "language", and has " Japan" in its top 100 by layer 11 (the logit lens: rank
 * 4 205). Tracking a token shows exactly that, per lens, per layer.
 */
import { useEffect, useState, type CSSProperties } from 'react';

import { revealSection } from '../../../layout/controller';
import { TokenPicker } from '../../llamacpp/lens/TokenPicker';
import { displayToken } from '../../llamacpp/lens/grid-model';
import type { LensGrid, LensSpec } from '../../llamacpp/api';
import { askInterp } from '../ask';
import { A } from '../attention/styles';
import { SUITE_SECTIONS, SUITE_VIEW } from '../ids';
import { S } from '../styles';
import {
  clearTrack,
  firstLayerWithin,
  openCompareTrace,
  readAt,
  refreshCompareTraces,
  track,
  useLensCompare,
} from './state';

export function LensCompare() {
  const st = useLensCompare();
  const [picking, setPicking] = useState(false);
  const trace = st.traces.find((t) => t.traceId === st.traceId);
  const anyGrid = Object.values(st.grids)[0] as LensGrid | undefined;
  const fitted = st.lenses.filter((l) => l.kind !== 'identity');

  useEffect(() => {
    void refreshCompareTraces();
  }, []);

  return (
    <div style={A.root}>
      <div style={C.toolbar}>
        <label style={C.field}>
          <span style={S.heading}>Trace</span>
          <select
            value={st.traceId ?? ''}
            onChange={(e) => void openCompareTrace(e.target.value)}
            aria-label="Trace to compare lenses on"
          >
            {st.traces.length === 0 && <option value="">no traces yet</option>}
            {st.traces.map((t) => (
              <option key={t.traceId} value={t.traceId}>
                {t.modelName} · {t.prompt.replace(/\s+/g, ' ').slice(0, 44)}
              </option>
            ))}
          </select>
        </label>
        <span style={S.mono}>
          {st.lenses.length
            ? `${st.lenses.length} lens${st.lenses.length === 1 ? '' : 'es'} for this model`
            : ''}
        </span>
        {trace && (
          <button type="button" onClick={() => setPicking(true)}>
            {st.track ? `Tracking ${JSON.stringify(st.track.text)}` : 'Track a token'}
          </button>
        )}
        {st.track && (
          <button type="button" className="btn-mini" onClick={clearTrack}>
            Clear
          </button>
        )}
      </div>
      {picking && trace && (
        <TokenPicker
          modelPath={trace.modelPath}
          label="Track this token across layers, under every lens"
          onPick={(entry) => {
            setPicking(false);
            void track(entry.id, entry.text);
          }}
          onCancel={() => setPicking(false)}
        />
      )}
      {st.error && <div style={A.conflict}>{st.error}</div>}
      {st.lenses.length > 0 && fitted.length === 0 && (
        <div style={A.note}>
          Only the logit lens exists for this model. Fitted Jacobian lenses come from the{' '}
          <button
            type="button"
            className="btn-mini"
            onClick={() => void revealSection(SUITE_SECTIONS.library, SUITE_VIEW)}
          >
            Library
          </button>{' '}
          (Neuronpedia publishes them for Gemma 3 1B IT, Qwen3, Llama 3.1 and more); install one for
          this GGUF and it appears here.
        </div>
      )}
      {anyGrid && <TokenStrip grid={anyGrid} />}
      {st.loading && !anyGrid && <p style={A.emptyText}>Reading…</p>}
      {anyGrid && (
        <div style={C.columns(st.lenses.length)}>
          {st.lenses.map((l) =>
            st.grids[l.id] ? <LensColumn key={l.id} spec={l} grid={st.grids[l.id]} /> : null,
          )}
        </div>
      )}
      {st.track && <TrackSummary />}
    </div>
  );
}

function TokenStrip({ grid }: { grid: LensGrid }) {
  const st = useLensCompare();
  const prompt = grid.tokens.filter((t) => !t.generated);
  return (
    <section style={{ ...A.ribbonWrap, paddingTop: 10 }} aria-label="Position">
      <div style={A.panelHead}>
        <span style={S.heading}>Position</span>
        <span style={S.mono}>every lens reads the residual of the selected token</span>
      </div>
      <div style={C.strip}>
        {prompt.map((t) => (
          <button
            key={t.index}
            type="button"
            className="interp-col"
            style={C.token(t.index === st.position)}
            onClick={() => void readAt(t.index)}
          >
            {t.text || '·'}
          </button>
        ))}
      </div>
    </section>
  );
}

function LensColumn({ spec, grid }: { spec: LensSpec; grid: LensGrid }) {
  const st = useLensCompare();
  const ranks = st.track?.ranks[spec.id];
  const first = ranks ? firstLayerWithin(ranks) : null;
  const rows = grid.layers
    .map((layer, i) => ({ layer, cell: grid.cells[i]?.[0] ?? null }))
    .reverse();
  return (
    <section
      style={{
        ...A.ribbonWrap,
        borderTopColor: spec.kind === 'identity' ? 'var(--border)' : 'var(--accent)',
      }}
      aria-label={spec.label}
    >
      <div style={A.panelHead}>
        <span style={S.heading}>{spec.kind === 'identity' ? 'Logit lens' : spec.label}</span>
      </div>
      <div style={S.mono} title={spec.provenance}>
        {spec.kind === 'identity'
          ? `verified: ${grid.verified}`
          : 'fitted J: reads what the layer holds, not what it would say'}
        {ranks && ` · ${JSON.stringify(st.track?.text)} in top 10 from L${first ?? '—'}`}
      </div>
      <ol style={C.rows}>
        {rows.map(({ layer, cell }, i) => {
          const rank = ranks?.[layer] ?? null;
          return (
            <li key={layer} style={{ ...C.row, ...S.stagger(i) }}>
              <span style={C.layer}>{layer < 0 ? 'emb' : `L${layer}`}</span>
              {cell ? (
                <span style={C.reads}>
                  {cell.texts.map((t, j) => (
                    <span key={j} style={j === 0 ? C.top : C.alt}>
                      {displayToken(t, 14)}
                    </span>
                  ))}
                </span>
              ) : (
                <span style={C.empty}>no map at this layer</span>
              )}
              {ranks && (
                <span style={C.rankCell} title={rank === null ? 'not read here' : `rank ${rank}`}>
                  <span
                    style={{ ...C.rankBar, width: `${rankWidth(rank, grid.unembedding.nVocab)}%` }}
                  />
                  <span style={C.rankText}>{rank === null ? '—' : rank.toLocaleString()}</span>
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** Rank as a bar: full at rank 1, empty at the bottom of the vocabulary (log scale). */
function rankWidth(rank: number | null, vocab: number): number {
  if (rank === null || rank < 1) return 0;
  return Math.max(2, Math.round((1 - Math.log(rank) / Math.log(Math.max(vocab, 2))) * 100));
}

function TrackSummary() {
  const st = useLensCompare();
  const t = st.track!;
  return (
    <section style={A.card} aria-label="Tracked token">
      <div style={A.cardHead}>
        <span style={A.cardLayer}>{JSON.stringify(t.text)}</span>
        <span style={S.mono}>first layer in the top 10, per lens</span>
      </div>
      <div style={C.summary}>
        {st.lenses.map((l) => {
          const first = firstLayerWithin(t.ranks[l.id] ?? {});
          return (
            <div key={l.id} style={A.fact}>
              <span style={A.factKey}>{l.kind === 'identity' ? 'logit lens' : l.label}</span>
              <span style={A.factValue}>{first === null ? 'never' : `L${first}`}</span>
            </div>
          );
        })}
      </div>
      <div style={A.actions}>
        <button
          type="button"
          onClick={() =>
            askInterp(
              `Lens comparison on trace ${st.traceId}, position ${st.position}, tracking ${JSON.stringify(t.text)}`,
            )
          }
        >
          Ask what this shows
        </button>
      </div>
    </section>
  );
}

const C = {
  toolbar: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 12,
    flexWrap: 'wrap',
  } satisfies CSSProperties,
  field: { display: 'grid', gap: 4, minWidth: 0, maxWidth: 380 } satisfies CSSProperties,
  strip: { display: 'flex', flexWrap: 'wrap', gap: 2 } satisfies CSSProperties,
  token(on: boolean): CSSProperties {
    return {
      height: 24,
      padding: '0 4px',
      border: 'none',
      borderRadius: 0,
      borderBottom: `2px solid ${on ? 'var(--accent)' : 'transparent'}`,
      background: on
        ? 'color-mix(in srgb, var(--accent) 14%, var(--bg-primary))'
        : 'var(--bg-primary)',
      color: 'var(--text-primary)',
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      whiteSpace: 'pre',
      cursor: 'pointer',
    };
  },
  columns(n: number): CSSProperties {
    return {
      display: 'grid',
      gridTemplateColumns: `repeat(${Math.max(1, n)}, minmax(0, 1fr))`,
      gap: 14,
      alignItems: 'start',
    };
  },
  rows: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'grid',
    gap: 1,
  } satisfies CSSProperties,
  row: {
    display: 'grid',
    gridTemplateColumns: '38px minmax(0, 1fr) auto',
    gap: 8,
    alignItems: 'center',
    minHeight: 22,
  } satisfies CSSProperties,
  layer: { ...S.mono, fontSize: 10, textAlign: 'right' } satisfies CSSProperties,
  reads: {
    display: 'flex',
    gap: 8,
    minWidth: 0,
    overflow: 'hidden',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,
  top: { fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700 } satisfies CSSProperties,
  alt: { ...S.mono, fontSize: 11 } satisfies CSSProperties,
  empty: { ...S.mono, fontSize: 10, color: 'var(--text-dim)' } satisfies CSSProperties,
  rankCell: {
    position: 'relative',
    width: 92,
    height: 14,
    background: 'var(--bg-primary)',
  } satisfies CSSProperties,
  rankBar: {
    position: 'absolute',
    inset: '0 auto 0 0',
    background: 'color-mix(in srgb, var(--accent) 55%, transparent)',
    transition: 'width 200ms ease',
  } satisfies CSSProperties,
  rankText: {
    position: 'relative',
    ...S.mono,
    fontSize: 10,
    paddingLeft: 4,
    color: 'var(--text-primary)',
  } satisfies CSSProperties,
  summary: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    gap: 4,
  } satisfies CSSProperties,
};
