/**
 * Attention: what every block's token mixer is, drawn per layer.
 *
 * The model explorer describes attention once — a head count, a head width, one
 * sliding window — and for the models worth looking at that one description is
 * wrong for most of their layers. Gemma 4 windows five of every six and gives the
 * sixth twice the head width and a quarter of the KV heads; Qwen3.5 replaces three
 * of every four with a recurrent state; Gemma 4 E2B's last twenty layers keep no
 * cache of their own. This section draws that, from the GGUF header
 * (`/api/interpretability/attention`), and says how each fact is known.
 *
 * Four views of one layout: the ribbon (one column per block), the selected
 * block's card with its mask, the KV cache the pattern buys, and how far back
 * information can reach by each depth.
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { revealSection } from '../../../layout/controller';
import { setModelLocus, useModelLocus } from '../../../model-locus';
import { RollingCounter } from '../../../viz/RollingCounter';
import { openInStepper } from '../../llamacpp/stepper/target';
import {
  interpretabilityStore,
  type AttentionLayout,
  type LayerAttention,
} from '../../interpretability/store';
import { askInterp } from '../ask';
import { SUITE_SECTIONS, SUITE_VIEW } from '../ids';
import { S } from '../styles';
import { SuiteFrame } from '../SuiteFrame';
import {
  fmtBytes,
  fmtContext,
  fmtReach,
  kindMeta,
  kindsPresent,
  maskCells,
  savings,
  shareArcs,
} from './model';
import { A } from './styles';

function useLayout(): AttentionLayout | null {
  const layout = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getAttention,
  );
  const arch = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getArchitecture,
  );
  // Per model, like the tensor inventory: a model switch re-reads once.
  const model = arch?.model ?? '';
  useEffect(() => {
    void interpretabilityStore.ensureAttention();
  }, [model]);
  return layout;
}

export function AttentionBody() {
  return (
    <SuiteFrame title="Attention">
      <AttentionSection />
    </SuiteFrame>
  );
}

export function AttentionSection() {
  const layout = useLayout();
  const locus = useModelLocus();

  if (!layout) {
    return <div style={A.empty}>Reading the model&apos;s header…</div>;
  }
  if (layout.error || layout.layers.length === 0) {
    return (
      <div style={A.empty}>
        <div style={S.heading}>No attention layout</div>
        <p style={A.emptyText}>
          {layout.error ?? 'The header lists no blocks.'} The layout is read from the GGUF file
          itself, so it needs a weights file this node can open — set{' '}
          <code>interpretability.ggufPath</code> for a server whose model store it cannot walk.
        </p>
      </div>
    );
  }

  const selected =
    typeof locus.layer === 'number' && locus.layer >= 0 && locus.layer < layout.layers.length
      ? layout.layers[locus.layer]
      : null;

  return (
    <div style={A.root}>
      <Header layout={layout} />
      <Ribbon layout={layout} selected={selected?.layer ?? null} />
      <div style={A.lower}>
        <div style={A.charts}>
          <KvCache layout={layout} />
          <Reach layout={layout} selected={selected?.layer ?? null} />
        </div>
        <LayerCard layer={selected} traceId={locus.traceId} />
      </div>
      {(layout.conflicts.length > 0 || layout.notes.length > 0) && (
        <div style={A.notes}>
          {layout.conflicts.map((c) => (
            <div key={c} style={A.conflict}>
              {c}
            </div>
          ))}
          {layout.notes.map((n) => (
            <div key={n} style={A.note}>
              {n}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── header ───────────────────────────────────────────────────────────────────

function Header({ layout }: { layout: AttentionLayout }) {
  return (
    <div style={A.header}>
      <div style={{ minWidth: 0 }}>
        <div style={S.heading}>Pattern</div>
        <div style={A.pattern}>{layout.pattern}</div>
        <div style={A.path} title={layout.path}>
          {layout.family ?? 'unknown family'} · {layout.path.split(/[\\/]/).pop()}
        </div>
      </div>
      <div style={A.legend}>
        {kindsPresent(layout).map((kind, i) => {
          const meta = kindMeta(kind);
          return (
            <span key={kind} style={{ ...A.legendItem, ...S.stagger(i) }} title={meta.gloss}>
              <span style={{ ...A.swatch, background: meta.color }} />
              {meta.short}
              <span style={A.legendCount}>{layout.counts[kind]}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

// ── ribbon ───────────────────────────────────────────────────────────────────

const ARC_H = 34;

function Ribbon({ layout, selected }: { layout: AttentionLayout; selected: number | null }) {
  const layers = layout.layers;
  const n = layers.length;
  const widest = Math.max(1, ...layers.map((l) => l.headDimK ?? 0));
  const arcs = useMemo(() => shareArcs(layers), [layers]);

  const pick = (layer: number) => setModelLocus({ layer }, 'attention');

  return (
    <section style={A.ribbonWrap} aria-label="Attention by layer">
      {arcs.length > 0 && (
        // Drawn in layer units, stretched to the ribbon's width; strokes stay
        // hairline at any width because they do not scale.
        <svg
          viewBox={`0 0 ${n} ${ARC_H}`}
          preserveAspectRatio="none"
          style={A.arcs}
          aria-hidden="true"
        >
          {arcs.map(({ from, to }) => {
            const x1 = from + 0.5;
            const x2 = to + 0.5;
            const lift = Math.min(ARC_H - 2, 6 + (x2 - x1) * 1.2);
            return (
              <path
                key={`${from}-${to}`}
                d={`M ${x1} ${ARC_H} C ${x1} ${ARC_H - lift}, ${x2} ${ARC_H - lift}, ${x2} ${ARC_H}`}
                fill="none"
                stroke={
                  selected === to || selected === from ? 'var(--accent)' : 'var(--text-faint)'
                }
                strokeWidth={selected === to || selected === from ? 1.5 : 1}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
        </svg>
      )}
      <div style={A.columns} role="listbox" aria-label="Blocks">
        {layers.map((layer, i) => {
          const meta = kindMeta(layer.kind);
          const height = layer.headDimK ? 0.35 + 0.65 * (layer.headDimK / widest) : 0.3;
          const on = selected === layer.layer;
          return (
            <button
              key={layer.layer}
              type="button"
              role="option"
              aria-selected={on}
              className="interp-col"
              title={columnTitle(layer)}
              onClick={() => pick(layer.layer)}
              style={{ ...A.column(on), ...S.stagger(Math.floor((i * 10) / n)) }}
            >
              <span
                style={{
                  ...A.bar,
                  height: `${height * 100}%`,
                  background: meta.color,
                  opacity: layer.kvSourceLayer !== null || layer.mtp ? 0.55 : 1,
                  outline: layer.basis === 'rule' ? '1px dashed var(--text-faint)' : undefined,
                }}
              />
            </button>
          );
        })}
      </div>
      <div style={A.marks} aria-hidden="true">
        {layers.map((layer) => (
          <span key={layer.layer} style={A.markCell}>
            {layer.kEqV && <span style={A.mark} title="K=V" />}
            {layer.outputGate && <span style={{ ...A.mark, ...A.markGate }} title="gated output" />}
            {layer.sinks && <span style={{ ...A.mark, ...A.markSink }} title="attention sinks" />}
            {layer.nope && <span style={{ ...A.mark, ...A.markNope }} title="no RoPE" />}
          </span>
        ))}
      </div>
      <div style={A.axis} aria-hidden="true">
        {layers.map((layer) => (
          <span key={layer.layer} style={A.axisCell}>
            {layer.layer % 5 === 0 || layer.layer === n - 1 ? layer.layer : ''}
          </span>
        ))}
      </div>
      <div style={A.key}>
        <span>height = head width</span>
        <span>faded = reads another layer&apos;s KV{arcs.length ? ' (arcs)' : ''}</span>
        <span>dashed = known by family rule</span>
        <span>
          <span style={A.mark} /> K=V <span style={{ ...A.mark, ...A.markGate }} /> gate{' '}
          <span style={{ ...A.mark, ...A.markSink }} /> sinks{' '}
          <span style={{ ...A.mark, ...A.markNope }} /> no RoPE
        </span>
      </div>
    </section>
  );
}

function columnTitle(l: LayerAttention): string {
  const meta = kindMeta(l.kind);
  const parts = [`L${l.layer} · ${meta.short}`];
  if (l.window) parts.push(`window ${l.window}`);
  if (l.heads) parts.push(`${l.heads}q/${l.kvHeads ?? '?'}kv × ${l.headDimK ?? '?'}`);
  if (l.kvSourceLayer !== null) parts.push(`KV from L${l.kvSourceLayer}`);
  if (l.mtp) parts.push('MTP');
  return parts.join(' · ');
}

// ── the selected layer ───────────────────────────────────────────────────────

function LayerCard({ layer, traceId }: { layer: LayerAttention | null; traceId?: string }) {
  if (!layer) {
    return (
      <aside style={A.card}>
        <div style={S.heading}>Block</div>
        <p style={A.emptyText}>Pick a column. The model explorer and the stepper follow.</p>
      </aside>
    );
  }
  const meta = kindMeta(layer.kind);
  const facts: [string, string][] = [];
  if (layer.window) facts.push(['window', `${layer.window.toLocaleString()} tokens`]);
  if (layer.heads) facts.push(['heads', `${layer.heads} q · ${layer.kvHeads ?? '?'} kv`]);
  if (layer.headDimK)
    facts.push([
      'head width',
      layer.headDimV && layer.headDimV !== layer.headDimK
        ? `K ${layer.headDimK} · V ${layer.headDimV}`
        : String(layer.headDimK),
    ]);
  if (layer.ropeBase)
    facts.push(['rope θ', layer.nope ? 'none (NoPE)' : layer.ropeBase.toLocaleString()]);
  if (layer.ropeDims) facts.push(['rope dims', String(layer.ropeDims)]);
  facts.push([
    'KV / token',
    layer.kvBytesPerToken
      ? fmtBytes(layer.kvBytesPerToken)
      : layer.kvSourceLayer !== null
        ? `shared (L${layer.kvSourceLayer})`
        : '— (state)',
  ]);
  facts.push(['reach', fmtReach(layer.reach)]);

  return (
    <aside style={A.card} aria-label={`Block ${layer.layer}`}>
      <div style={A.cardHead}>
        <span style={A.cardLayer}>L{layer.layer}</span>
        <span style={{ ...A.swatch, background: meta.color }} />
        <span style={S.heading}>{meta.label}</span>
      </div>
      <div style={A.cardBody}>
        <Mask kind={layer.kind} />
        <div style={A.facts}>
          {facts.map(([k, v]) => (
            <div key={k} style={A.fact}>
              <span style={A.factKey}>{k}</span>
              <span style={A.factValue}>{v}</span>
            </div>
          ))}
        </div>
      </div>
      <p style={A.gloss}>{meta.gloss}</p>
      <div style={A.flags}>
        <span style={A.basis(layer.basis)} title="How this was known">
          {layer.basis}
        </span>
        {layer.kEqV && <span style={A.flag}>K=V</span>}
        {layer.outputGate && <span style={A.flag}>gated output</span>}
        {layer.sinks && <span style={A.flag}>sinks</span>}
        {layer.mtp && <span style={A.flag}>MTP head</span>}
      </div>
      <div style={A.actions}>
        <button type="button" onClick={() => revealSection(SUITE_SECTIONS.model, SUITE_VIEW)}>
          Open in Model
        </button>
        {/* The locus is this block, and `askInterp` names it — repeating it in the
            subject read "block L5, at L5". */}
        <button type="button" onClick={() => askInterp('attention layout')}>
          Ask why
        </button>
        <button
          type="button"
          disabled={!traceId}
          title={traceId ? undefined : 'Record a trace first — the stepper reads one'}
          onClick={() => traceId && openInStepper({ traceId, layer: layer.layer })}
        >
          Step this block
        </button>
      </div>
    </aside>
  );
}

const MASK_N = 12;

/** A small causal mask: which keys (columns) each query (row) may see. */
function Mask({ kind }: { kind: string }) {
  const cells = maskCells(kind, MASK_N);
  const meta = kindMeta(kind);
  const size = 6;
  return (
    <svg
      width={MASK_N * size}
      height={MASK_N * size}
      viewBox={`0 0 ${MASK_N * size} ${MASK_N * size}`}
      style={A.mask}
      role="img"
      aria-label={`${meta.short} mask`}
    >
      {cells ? (
        cells.flatMap((row, q) =>
          row.map((on, k) => (
            <rect
              key={`${q}-${k}`}
              x={k * size}
              y={q * size}
              width={size - 1}
              height={size - 1}
              fill={on ? meta.color : 'var(--bg-primary)'}
            />
          )),
        )
      ) : (
        // A recurrent state: no matrix to draw, one carried vector.
        <g>
          {Array.from({ length: MASK_N }, (_, i) => (
            <rect
              key={i}
              x={i * size}
              y={(MASK_N / 2) * size - size}
              width={size - 1}
              height={size * 2}
              fill={meta.color}
              opacity={0.35 + (0.65 * i) / MASK_N}
            />
          ))}
        </g>
      )}
    </svg>
  );
}

// ── KV cache ─────────────────────────────────────────────────────────────────

function KvCache({ layout }: { layout: AttentionLayout }) {
  const points = layout.kvCache;
  const [at, setAt] = useState(() => Math.min(points.length - 1, 3));
  if (!points.length) return null;
  const point = points[Math.min(at, points.length - 1)];
  const ratio = savings(point);

  return (
    <section style={A.panel}>
      <div style={A.panelHead}>
        <span style={S.heading}>KV cache (fp16)</span>
        <span style={A.big}>
          <RollingCounter value={point.bytes} format={fmtBytes} />
        </span>
        <span style={S.mono}>
          at {fmtContext(point.context)} ·{' '}
          {ratio && ratio > 1.05
            ? `${ratio.toFixed(1)}× smaller than all-global`
            : 'same as all-global'}
        </span>
      </div>
      <div style={A.bars}>
        {points.map((p, i) => (
          <button
            key={p.context}
            type="button"
            className="interp-col"
            style={A.barGroup(i === at)}
            onClick={() => setAt(i)}
            title={`${fmtContext(p.context)}: ${fmtBytes(p.bytes)} (all-global ${fmtBytes(p.bytesIfGlobal)})`}
          >
            {/* Each pair on its own scale: the all-global cache is the full bar.
                One shared scale let 256k flatten 1k–32k to nothing, and the
                ratio — the thing this chart is for — is per context anyway. */}
            <span style={A.barPair}>
              <span style={{ ...A.barGhost, height: '100%' }} />
              <span
                style={{
                  ...A.barReal,
                  height: `${p.bytesIfGlobal ? (p.bytes / p.bytesIfGlobal) * 100 : 0}%`,
                }}
              />
            </span>
            <span style={A.barLabel}>{fmtContext(p.context)}</span>
            <span style={A.barValue}>{fmtBytes(p.bytes)}</span>
          </button>
        ))}
      </div>
      <div style={A.key}>
        <span>
          <span style={{ ...A.swatch, background: 'var(--accent)' }} /> as built
        </span>
        <span>
          <span style={{ ...A.swatch, ...A.ghostSwatch }} /> if every attention layer were global
          and kept its own cache (each bar on its own scale)
        </span>
        <span>estimate: K and V at fp16, K=V counted once</span>
      </div>
    </section>
  );
}

// ── receptive field ──────────────────────────────────────────────────────────

function Reach({ layout, selected }: { layout: AttentionLayout; selected: number | null }) {
  const layers = layout.layers.filter((l) => !l.mtp);
  const n = layers.length;
  const finite = layers.map((l) => l.reach).filter((r): r is number => r !== null);
  const ceiling = Math.max(2, ...finite) * 4;
  const H = 60;
  const y = (r: number | null) =>
    r === null ? 2 : H - 2 - ((H - 6) * Math.log2(1 + r)) / Math.log2(1 + ceiling);
  const path = layers
    .map((l, i) => `${i === 0 ? 'M' : 'L'} ${i} ${y(l.reach)} L ${i + 1} ${y(l.reach)}`)
    .join(' ');
  const firstFull = layers.find((l) => l.reach === null);

  return (
    <section style={A.panel}>
      <div style={A.panelHead}>
        <span style={S.heading}>How far back by each depth</span>
        <span style={S.mono}>
          {firstFull
            ? `whole context from L${firstFull.layer}`
            : `≤ ${(finite[finite.length - 1] ?? 0).toLocaleString()} tokens at the top`}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${n} ${H}`}
        preserveAspectRatio="none"
        style={A.reach}
        role="img"
        aria-label="Receptive field by layer"
      >
        <path
          d={path}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
        {selected !== null && selected < n && (
          <line
            x1={selected + 0.5}
            x2={selected + 0.5}
            y1={0}
            y2={H}
            stroke="var(--text-dim)"
            strokeDasharray="2 2"
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      <div style={A.key}>
        <span>top line = the whole context; log scale below it; an upper bound</span>
      </div>
    </section>
  );
}
