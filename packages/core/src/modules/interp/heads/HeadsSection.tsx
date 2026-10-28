/**
 * Heads: every attention head of a traced pass, scored, so the few worth looking
 * at can be found before opening any matrix — HeadVis, for a local model.
 *
 * The finder is a layer × head grid shaded by one metric; beside it, the heads that
 * score highest; below, the selected head's card. Induction and duplicate need a
 * prompt that repeats something, so on any other trace they are greyed out and the
 * probe — a random word sequence, twice — is one click away.
 *
 * Layer kinds (sliding / global …) come from the attention layout, and are shown
 * only when that layout was read from the file this trace was recorded on.
 */
import { useEffect, useMemo, useState, useSyncExternalStore, type CSSProperties } from 'react';

import { setModelLocus, useModelLocus } from '../../../model-locus';
import { RollingCounter } from '../../../viz/RollingCounter';
import { interpretabilityStore } from '../../interpretability/store';
import { getLlamaModels, type HeadScore, type ModelEntry } from '../../llamacpp/api';
import { openInStepper } from '../../llamacpp/stepper/target';
import { askInterp } from '../ask';
import { kindMeta, sameFile } from '../attention/model';
import { A } from '../attention/styles';
import { S } from '../styles';
import { SuiteFrame } from '../SuiteFrame';
import {
  METRICS,
  measurable,
  openTrace,
  rankHeads,
  refreshTraces,
  runProbe,
  scoreOf,
  selectHead,
  setMetric,
  useHeads,
  defaultProbeModel,
  type HeadMetric,
} from './state';

export function HeadsBody() {
  return (
    <SuiteFrame title="Heads">
      <HeadsSection />
    </SuiteFrame>
  );
}

/** The kind of `layer`, when the attention layout describes this trace's file. */
function useLayerKinds(modelPath: string | undefined): ((layer: number) => string | null) | null {
  const layout = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getAttention,
  );
  useEffect(() => {
    void interpretabilityStore.ensureAttention();
  }, []);
  if (!layout || layout.error || !sameFile(layout.path, modelPath)) return null;
  return (layer) => layout.layers[layer]?.kind ?? null;
}

export function HeadsSection() {
  const st = useHeads();
  const locus = useModelLocus();
  const table = st.table;
  const kindOf = useLayerKinds(table?.modelPath);

  useEffect(() => {
    void refreshTraces();
  }, []);

  // Selecting a layer elsewhere (the attention ribbon, the explorer) moves the
  // grid's row highlight; selecting a head here moves the locus for them.
  const pickHead = (h: HeadScore) => {
    selectHead(h.layer, h.head);
    setModelLocus({ layer: h.layer, traceId: table?.traceId }, 'heads');
  };

  return (
    <div style={A.root}>
      <Toolbar />
      {!table ? (
        <div style={A.emptyText}>
          {st.loading ? 'Scoring heads…' : 'Pick a trace with attention, or run the probe.'}
        </div>
      ) : table.error ? (
        <div style={A.conflict}>{table.error}</div>
      ) : (
        <>
          <MetricBar table={table} />
          <div style={H.layout}>
            <Grid
              heads={table.heads ?? []}
              layers={table.layers ?? 0}
              perLayer={table.headsPerLayer ?? 0}
              metric={st.metric}
              unmeasurable={!measurable(table, st.metric)}
              selected={st.selected}
              locusLayer={typeof locus.layer === 'number' ? locus.layer : null}
              onPick={pickHead}
            />
            <Ranking
              heads={table.heads ?? []}
              metric={st.metric}
              kindOf={kindOf}
              selected={st.selected}
              onPick={pickHead}
              unmeasurable={!measurable(table, st.metric)}
            />
          </div>
          <HeadCard
            head={
              st.selected
                ? ((table.heads ?? []).find(
                    (h) => h.layer === st.selected!.layer && h.head === st.selected!.head,
                  ) ?? null)
                : null
            }
            kind={st.selected && kindOf ? kindOf(st.selected.layer) : null}
            traceId={table.traceId}
            induction={measurable(table, 'induction')}
          />
          {!kindOf && (
            <div style={A.note}>
              Layer kinds are not shown: the attention layout describes the agent&apos;s loaded
              model, and this trace was recorded on {table.model || 'another file'}.
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── toolbar: the trace, and the probe ────────────────────────────────────────

function Toolbar() {
  const st = useHeads();
  const [models, setModels] = useState<ModelEntry[]>([]);
  // `picked` is the user's own choice, which then sticks. Until they make one, the
  // default follows the shown trace — the catalog usually arrives before the trace
  // opens, and a default computed once then would never see which model it is.
  const [picked, setPicked] = useState('');
  const tracePath = st.table?.modelPath;
  const modelPath =
    picked || defaultProbeModel(models, tracePath, interpretabilityStore.getAttention()?.path);
  const setModelPath = setPicked;

  useEffect(() => {
    let alive = true;
    void getLlamaModels()
      .then((m) => {
        if (alive) setModels(m.models.filter((x) => !x.isAdapter && !x.error));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div style={H.toolbar}>
      <label style={H.field}>
        <span style={S.heading}>Trace</span>
        <select
          value={st.traceId ?? ''}
          onChange={(e) => void openTrace(e.target.value)}
          aria-label="Trace to score"
        >
          {st.traces.length === 0 && <option value="">no attention traces yet</option>}
          {st.traces.map((t) => (
            <option key={t.traceId} value={t.traceId}>
              {t.modelName} · {t.prompt.slice(0, 40)}
            </option>
          ))}
        </select>
      </label>
      <label style={H.field}>
        <span style={S.heading}>Probe on</span>
        <select
          value={modelPath}
          onChange={(e) => setModelPath(e.target.value)}
          aria-label="Model to probe"
        >
          {models.map((m) => (
            <option key={m.path} value={m.path}>
              {m.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        disabled={!modelPath || st.probe.running}
        title="Trace a random word sequence twice, with attention on — the prompt induction needs"
        onClick={() => void runProbe(modelPath)}
      >
        {st.probe.running ? 'Probing…' : 'Run induction probe'}
      </button>
      {(st.probe.running || st.probe.error) && (
        <span style={st.probe.error ? H.error : S.mono}>{st.probe.error ?? st.probe.status}</span>
      )}
    </div>
  );
}

// ── metric chooser ───────────────────────────────────────────────────────────

function MetricBar({ table }: { table: NonNullable<ReturnType<typeof useHeads>['table']> }) {
  const st = useHeads();
  const gloss = METRICS.find((m) => m.id === st.metric)?.gloss;
  return (
    <div>
      <div style={H.metrics} role="tablist" aria-label="Score">
        {METRICS.map((m) => {
          const ok = measurable(table, m.id);
          return (
            <button
              key={m.id}
              type="button"
              role="tab"
              className="interp-subtab"
              aria-selected={st.metric === m.id}
              title={
                ok
                  ? m.gloss
                  : 'This trace repeats nothing, so this is not measurable — run the probe'
              }
              style={{ ...S.subTab(st.metric === m.id), opacity: ok ? 1 : 0.45 }}
              onClick={() => setMetric(m.id)}
            >
              {m.label}
            </button>
          );
        })}
      </div>
      <p style={A.gloss}>
        {gloss}{' '}
        <span style={S.mono}>
          {table.tokens} tokens · {table.repeatedPositions} repeated positions · {table.layers}×
          {table.headsPerLayer} heads
        </span>
      </p>
    </div>
  );
}

// ── the grid ─────────────────────────────────────────────────────────────────

function Grid({
  heads,
  layers,
  perLayer,
  metric,
  unmeasurable,
  selected,
  locusLayer,
  onPick,
}: {
  heads: HeadScore[];
  layers: number;
  perLayer: number;
  metric: HeadMetric;
  unmeasurable: boolean;
  selected: { layer: number; head: number } | null;
  locusLayer: number | null;
  onPick: (h: HeadScore) => void;
}) {
  // Entropy is in nats, not a 0–1 weight: shade it against the table's own maximum.
  const top = useMemo(() => {
    const values = heads.map((h) => scoreOf(h, metric)).filter((v): v is number => v !== null);
    return metric === 'entropy' ? Math.max(1e-9, ...values) : 1;
  }, [heads, metric]);
  const byKey = useMemo(() => new Map(heads.map((h) => [`${h.layer}:${h.head}`, h])), [heads]);

  return (
    <section style={{ ...A.panel, borderTopColor: 'var(--accent)' }} aria-label="Heads by layer">
      <div style={A.panelHead}>
        <span style={S.heading}>Layer × head</span>
        <span style={S.mono}>
          {unmeasurable ? 'not measurable on this trace' : 'darker = higher'}
        </span>
      </div>
      <div style={H.grid(perLayer)} role="grid">
        {Array.from({ length: layers }, (_, layer) => (
          <div key={layer} role="row" style={H.row(layer === locusLayer)}>
            <span style={H.rowLabel}>{layer}</span>
            {Array.from({ length: perLayer }, (_, head) => {
              const h = byKey.get(`${layer}:${head}`);
              const value = h ? scoreOf(h, metric) : null;
              const on = selected?.layer === layer && selected.head === head;
              return (
                <button
                  key={head}
                  type="button"
                  role="gridcell"
                  className="interp-col"
                  aria-selected={on}
                  title={
                    h
                      ? `L${layer} H${head} · ${value === null ? 'not measurable' : value.toFixed(3)}`
                      : `L${layer} H${head}`
                  }
                  onClick={() => h && onPick(h)}
                  style={H.cell(value === null ? null : value / top, on)}
                />
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}

// ── the ranking ──────────────────────────────────────────────────────────────

function Ranking({
  heads,
  metric,
  kindOf,
  selected,
  onPick,
  unmeasurable,
}: {
  heads: HeadScore[];
  metric: HeadMetric;
  kindOf: ((layer: number) => string | null) | null;
  selected: { layer: number; head: number } | null;
  onPick: (h: HeadScore) => void;
  unmeasurable: boolean;
}) {
  const ranked = rankHeads(heads, metric, 12);
  return (
    <section style={A.panel} aria-label="Highest-scoring heads">
      <div style={A.panelHead}>
        <span style={S.heading}>Top heads</span>
      </div>
      {unmeasurable ? (
        <p style={A.emptyText}>
          This trace repeats nothing, so there is no earlier copy for a head to look at. Run the
          induction probe.
        </p>
      ) : (
        <ol style={H.list}>
          {ranked.map((h, i) => {
            const kind = kindOf?.(h.layer) ?? null;
            const on = selected?.layer === h.layer && selected.head === h.head;
            return (
              <li key={`${h.layer}:${h.head}`} style={S.stagger(i)}>
                <button
                  type="button"
                  className="interp-col"
                  style={H.rankRow(on)}
                  onClick={() => onPick(h)}
                >
                  <span style={H.rankId}>
                    L{h.layer} H{h.head}
                  </span>
                  {kind && (
                    <span style={H.kindTag}>
                      <span style={{ ...A.swatch, background: kindMeta(kind).color }} />
                      {kindMeta(kind).short}
                    </span>
                  )}
                  <span style={H.rankValue}>{(scoreOf(h, metric) ?? 0).toFixed(3)}</span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

// ── the selected head ────────────────────────────────────────────────────────

function HeadCard({
  head,
  kind,
  traceId,
  induction,
}: {
  head: HeadScore | null;
  kind: string | null;
  traceId?: string;
  induction: boolean;
}) {
  if (!head) {
    return (
      <aside style={A.card}>
        <div style={S.heading}>Head</div>
        <p style={A.emptyText}>Pick a cell or a ranked head.</p>
      </aside>
    );
  }
  const bars: [string, number | null][] = [
    ['induction', induction ? head.induction : null],
    ['previous token', head.prevToken],
    ['duplicate', induction ? head.duplicate : null],
    ['sink', head.sink],
    ['self', head.selfWeight],
  ];
  return (
    <aside style={A.card} aria-label={`Layer ${head.layer} head ${head.head}`}>
      <div style={A.cardHead}>
        <span style={A.cardLayer}>
          L{head.layer} H{head.head}
        </span>
        {kind && (
          <span style={H.kindTag}>
            <span style={{ ...A.swatch, background: kindMeta(kind).color }} />
            {kindMeta(kind).label}
          </span>
        )}
      </div>
      <div style={A.facts}>
        {bars.map(([label, value]) => (
          <div key={label} style={H.barRow}>
            <span style={A.factKey}>{label}</span>
            <span style={H.barTrack}>
              <span style={{ ...H.barFill, width: `${Math.round((value ?? 0) * 100)}%` }} />
            </span>
            <span style={A.factValue}>
              {value === null ? (
                'n/a'
              ) : (
                <RollingCounter value={value} format={(v) => v.toFixed(2)} />
              )}
            </span>
          </div>
        ))}
        <div style={A.fact}>
          <span style={A.factKey}>entropy</span>
          <span style={A.factValue}>{head.entropy.toFixed(2)} nats</span>
        </div>
        <div style={A.fact}>
          <span style={A.factKey}>rows scored</span>
          <span style={A.factValue}>{head.rows}</span>
        </div>
      </div>
      <div style={A.actions}>
        <button
          type="button"
          disabled={!traceId}
          onClick={() => traceId && openInStepper({ traceId, layer: head.layer })}
        >
          Step this layer
        </button>
        <button
          type="button"
          onClick={() => askInterp(`Heads section, head L${head.layer} H${head.head}`)}
        >
          Ask what it does
        </button>
      </div>
    </aside>
  );
}

// ── styles local to this section ─────────────────────────────────────────────

const H = {
  toolbar: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 12,
    flexWrap: 'wrap',
  } satisfies CSSProperties,
  field: { display: 'grid', gap: 4, minWidth: 0 } satisfies CSSProperties,
  error: { ...S.mono, color: 'var(--danger)' } satisfies CSSProperties,
  metrics: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 2,
    borderBottom: '1px solid var(--border)',
  } satisfies CSSProperties,
  layout: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1.8fr) minmax(220px, 1fr)',
    gap: 14,
    alignItems: 'start',
  } satisfies CSSProperties,
  grid(perLayer: number): CSSProperties {
    return {
      display: 'grid',
      gap: 1,
      gridTemplateRows: 'auto',
      ['--heads' as string]: String(perLayer),
    };
  },
  row(onLocus: boolean): CSSProperties {
    return {
      display: 'grid',
      gridTemplateColumns: '26px repeat(var(--heads), minmax(4px, 1fr))',
      gap: 1,
      alignItems: 'center',
      outline: onLocus ? '1px solid var(--accent)' : undefined,
    };
  },
  rowLabel: {
    ...S.mono,
    fontSize: 9,
    textAlign: 'right',
    paddingRight: 4,
  } satisfies CSSProperties,
  cell(value: number | null, on: boolean): CSSProperties {
    return {
      height: 10,
      padding: 0,
      border: 'none',
      borderRadius: 0,
      cursor: 'pointer',
      background:
        value === null
          ? 'repeating-linear-gradient(45deg, var(--bg-primary) 0 2px, var(--border) 2px 3px)'
          : `color-mix(in srgb, var(--accent) ${Math.round(Math.min(1, Math.max(0, value)) * 100)}%, var(--bg-primary))`,
      outline: on ? '1px solid var(--text-primary)' : undefined,
      outlineOffset: on ? 0 : undefined,
    };
  },
  list: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'grid',
    gap: 2,
  } satisfies CSSProperties,
  rankRow(on: boolean): CSSProperties {
    return {
      display: 'flex',
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
  rankId: {
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    fontWeight: 700,
    minWidth: 64,
  } satisfies CSSProperties,
  kindTag: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    ...S.mono,
    fontSize: 10,
  } satisfies CSSProperties,
  rankValue: {
    ...S.mono,
    marginLeft: 'auto',
    color: 'var(--text-primary)',
  } satisfies CSSProperties,
  barRow: {
    display: 'grid',
    gridTemplateColumns: '96px 1fr 44px',
    alignItems: 'center',
    gap: 8,
  } satisfies CSSProperties,
  barTrack: {
    height: 6,
    background: 'var(--bg-primary)',
    position: 'relative',
  } satisfies CSSProperties,
  barFill: {
    position: 'absolute',
    inset: '0 auto 0 0',
    background: 'var(--accent)',
    transition: 'width 200ms ease',
  } satisfies CSSProperties,
};
