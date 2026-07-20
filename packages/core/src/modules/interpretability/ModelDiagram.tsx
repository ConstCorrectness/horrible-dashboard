import { useSyncExternalStore } from 'react';

import {
  interpretabilityStore,
  type AttentionSpec,
  type ModelArchitecture,
  type MoeSpec,
} from './store';

function useArchitecture(): ModelArchitecture | null {
  return useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getArchitecture,
  );
}

function fmtCount(n: number | null): string {
  if (n == null) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}k`;
  return String(n);
}

const ATTENTION_LABEL: Record<string, string> = {
  mha: 'Multi-head attention',
  gqa: 'Grouped-query attention',
  mqa: 'Multi-query attention',
  unknown: 'Attention',
};

/**
 * Query heads drawn over the KV heads they share.
 *
 * This is the one part of a transformer whose cost isn't obvious from a number:
 * "32 heads / 8 KV heads" is a 4× smaller KV cache than full MHA, and seeing four
 * query ticks sitting on each KV block makes that immediate. Capped at a readable
 * number of groups — beyond that the pattern is established and the ratio label
 * carries the rest.
 */
function HeadGrouping({ attention, width }: { attention: AttentionSpec; width: number }) {
  const kv = attention.kvHeads;
  const ratio = attention.groupRatio;
  if (!kv || !ratio) return null;

  const shownGroups = Math.min(kv, 8);
  const truncated = kv > shownGroups;
  const gap = 4;
  const groupW = (width - gap * (shownGroups - 1)) / shownGroups;

  return (
    <g>
      {Array.from({ length: shownGroups }, (_, g) => {
        const x = g * (groupW + gap);
        const qCount = Math.min(ratio, 8);
        const qW = Math.max(1.5, (groupW - (qCount - 1) * 1.5) / qCount);
        return (
          <g key={g}>
            {/* Query heads: the ticks sharing this group's KV. */}
            {Array.from({ length: qCount }, (_, q) => (
              <rect
                key={q}
                x={x + q * (qW + 1.5)}
                y={0}
                width={qW}
                height={7}
                rx={1}
                className="md-qhead"
              />
            ))}
            {/* The shared KV head underneath them. */}
            <rect x={x} y={9} width={groupW} height={5} rx={1} className="md-kvhead" />
          </g>
        );
      })}
      {truncated && (
        <text x={width + 4} y={11} className="md-micro">
          …
        </text>
      )}
    </g>
  );
}

/** Expert bank with the routed top-k highlighted. */
function ExpertBank({ moe, width }: { moe: MoeSpec; width: number }) {
  const total = moe.experts ?? 0;
  if (!total) return null;
  const shown = Math.min(total, 12);
  const active = Math.min(moe.expertsPerToken, shown);
  const gap = 3;
  const w = (width - gap * (shown - 1)) / shown;
  return (
    <g>
      {Array.from({ length: shown }, (_, i) => (
        <rect
          key={i}
          x={i * (w + gap)}
          y={0}
          width={w}
          height={12}
          rx={2}
          className={i < active ? 'md-expert-active' : 'md-expert'}
        />
      ))}
      {total > shown && (
        <text x={width + 4} y={10} className="md-micro">
          …
        </text>
      )}
    </g>
  );
}

interface BoxProps {
  x: number;
  y: number;
  w: number;
  h: number;
  className?: string;
}

function Box({ x, y, w, h, className = '' }: BoxProps) {
  return <rect x={x} y={y} width={w} height={h} rx={4} className={`md-box ${className}`} />;
}

/** Vertical arrow between stages. */
function Flow({ x, y, h }: { x: number; y: number; h: number }) {
  return (
    <g className="md-flow">
      <line x1={x} y1={y} x2={x} y2={y + h - 4} />
      <polygon points={`${x - 3},${y + h - 4} ${x + 3},${y + h - 4} ${x},${y + h}`} />
    </g>
  );
}

/**
 * The model as a 2D stack: embedding → N identical blocks → head.
 *
 * The block is drawn once with a "×N" multiplier rather than repeated N times.
 * Forty-two identical rectangles convey nothing that the number doesn't, and the
 * space is better spent on what differs *inside* one block — the head grouping,
 * the FFN expansion, the expert routing.
 */
export function ModelDiagram() {
  const arch = useArchitecture();

  if (!arch || arch.error) {
    return (
      <div className="interp-empty">
        <p>No model architecture available.</p>
        <p className="interp-dim">
          {arch?.error ??
            'Load a model and open a turn — the diagram describes whichever model the captured turns ran on.'}
        </p>
      </div>
    );
  }

  const { attention: attn, ffn, moe } = arch;
  const W = 300;
  const PAD = 12;
  const inner = W - PAD * 2;

  // Lay the stack out top-down, growing as sections are present. Computing y as we
  // go (rather than fixed coordinates) is what lets absent sections vanish cleanly
  // instead of leaving holes.
  let y = 8;
  const rows: React.ReactNode[] = [];
  const push = (node: React.ReactNode, height: number, flow = true) => {
    rows.push(node);
    y += height;
    if (flow) {
      rows.push(<Flow key={`f${y}`} x={W / 2} y={y} h={14} />);
      y += 14;
    }
  };

  // ── Embedding ─────────────────────────────────────────────────────────────
  push(
    <g key="emb">
      <Box x={PAD} y={y} w={inner} h={34} className="md-io" />
      <text x={W / 2} y={y + 15} className="md-label">
        Token embedding
      </text>
      <text x={W / 2} y={y + 27} className="md-sub">
        {fmtCount(arch.vocabSize)} vocab × {arch.hiddenSize ?? '—'}
      </text>
    </g>,
    34,
  );

  // ── The repeated block ────────────────────────────────────────────────────
  const blockTop = y;
  let by = y + 22; // room for the "×N" header inside the block frame
  const blockRows: React.ReactNode[] = [];

  const sub = (node: React.ReactNode, height: number) => {
    blockRows.push(node);
    by += height + 8;
  };

  if (arch.normType) {
    sub(
      <g key="n1">
        <Box x={PAD + 10} y={by} w={inner - 20} h={16} className="md-norm" />
        <text x={W / 2} y={by + 12} className="md-sub">
          {arch.normType === 'rmsnorm' ? 'RMSNorm' : arch.normType}
        </text>
      </g>,
      16,
    );
  }

  if (attn) {
    const h = attn.kvHeads && attn.groupRatio ? 54 : 34;
    sub(
      <g key="attn">
        <Box x={PAD + 10} y={by} w={inner - 20} h={h} className="md-attn" />
        <text x={W / 2} y={by + 14} className="md-label">
          {ATTENTION_LABEL[attn.kind] ?? 'Attention'}
        </text>
        <text x={W / 2} y={by + 26} className="md-sub">
          {attn.heads ?? '—'} heads
          {attn.kvHeads != null && attn.kvHeads !== attn.heads ? ` / ${attn.kvHeads} KV` : ''}
          {attn.headDim ? ` · dim ${attn.headDimDerived ? '~' : ''}${attn.headDim}` : ''}
        </text>
        {attn.kvHeads && attn.groupRatio ? (
          <g transform={`translate(${PAD + 26}, ${by + 32})`}>
            <HeadGrouping attention={attn} width={inner - 52} />
          </g>
        ) : null}
      </g>,
      h,
    );
  }

  sub(
    <g key="r1">
      <text x={W / 2} y={by + 10} className="md-residual">
        ⊕ residual
      </text>
    </g>,
    12,
  );

  if (arch.normType) {
    sub(
      <g key="n2">
        <Box x={PAD + 10} y={by} w={inner - 20} h={16} className="md-norm" />
        <text x={W / 2} y={by + 12} className="md-sub">
          {arch.normType === 'rmsnorm' ? 'RMSNorm' : arch.normType}
        </text>
      </g>,
      16,
    );
  }

  if (moe) {
    const h = 62;
    sub(
      <g key="moe">
        <Box x={PAD + 10} y={by} w={inner - 20} h={h} className="md-moe" />
        <text x={W / 2} y={by + 14} className="md-label">
          Mixture of experts
        </text>
        <text x={W / 2} y={by + 26} className="md-sub">
          router → top-{moe.expertsPerToken} of {moe.experts}
          {moe.activeFraction != null ? ` · ${Math.round(moe.activeFraction * 100)}% active` : ''}
        </text>
        <g transform={`translate(${PAD + 26}, ${by + 32})`}>
          <ExpertBank moe={moe} width={inner - 52} />
        </g>
        {moe.expertIntermediateSize ? (
          <text x={W / 2} y={by + 57} className="md-micro">
            each expert {arch.hiddenSize ?? '—'} → {moe.expertIntermediateSize} →{' '}
            {arch.hiddenSize ?? '—'}
          </text>
        ) : null}
        {moe.sharedExperts ? (
          <text x={W / 2} y={by + 57} className="md-micro">
            + {moe.sharedExperts} shared expert{moe.sharedExperts === 1 ? '' : 's'} (always on)
          </text>
        ) : null}
      </g>,
      h,
    );
  } else if (ffn) {
    sub(
      <g key="ffn">
        <Box x={PAD + 10} y={by} w={inner - 20} h={36} className="md-ffn" />
        <text x={W / 2} y={by + 14} className="md-label">
          {ffn.gated ? 'Gated FFN' : 'Feed-forward'}
          {ffn.activation ? ` · ${ffn.activation}` : ''}
        </text>
        <text x={W / 2} y={by + 28} className="md-sub">
          {arch.hiddenSize ?? '—'} → {ffn.intermediateSize ?? '—'} → {arch.hiddenSize ?? '—'}
          {ffn.expansionRatio ? ` (${ffn.expansionRatio}×)` : ''}
        </text>
      </g>,
      36,
    );
  }

  sub(
    <g key="r2">
      <text x={W / 2} y={by + 10} className="md-residual">
        ⊕ residual
      </text>
    </g>,
    12,
  );

  const blockH = by - blockTop;
  push(
    <g key="block">
      <rect x={PAD} y={blockTop} width={inner} height={blockH} rx={6} className="md-blockframe" />
      <text x={PAD + 8} y={blockTop + 15} className="md-blocktitle">
        Transformer block ×{arch.layers ?? '?'}
      </text>
      {blockRows}
    </g>,
    blockH,
  );

  // ── Output head ───────────────────────────────────────────────────────────
  push(
    <g key="head">
      <Box x={PAD} y={y} w={inner} h={34} className="md-io" />
      <text x={W / 2} y={y + 15} className="md-label">
        Final norm → LM head
      </text>
      <text x={W / 2} y={y + 27} className="md-sub">
        {arch.hiddenSize ?? '—'} → {fmtCount(arch.vocabSize)}
        {arch.tiedEmbeddings ? ' · tied' : ''}
      </text>
    </g>,
    34,
    false,
  );

  const height = y + 10;

  return (
    <div className="md-root">
      <div className="md-head">
        <span className="interp-model">{arch.model}</span>
        {arch.family && <span className="interp-chip interp-kind-agent">{arch.family}</span>}
        {arch.parameterCount && (
          <span className="interp-dim">{fmtCount(arch.parameterCount)} params</span>
        )}
        <span
          className={arch.source === 'ollama' ? 'interp-dim' : 'interp-approx-chip'}
          title={
            arch.source === 'ollama'
              ? `Read from the loaded weights' GGUF metadata (${arch.sourceDetail})`
              : `Read from ${arch.sourceDetail}'s config.json — describes the architecture, ` +
                'not necessarily the exact build your server loaded.'
          }
        >
          {arch.source === 'ollama' ? 'from weights' : 'from repo'}
        </span>
      </div>

      <svg
        viewBox={`0 0 ${W} ${height}`}
        className="md-svg"
        role="img"
        aria-label="Model architecture diagram"
      >
        {rows}
      </svg>

      <dl className="md-facts">
        {arch.contextLength != null && (
          <>
            <dt>Context</dt>
            <dd>{fmtCount(arch.contextLength)}</dd>
          </>
        )}
        {attn?.slidingWindow != null && (
          <>
            <dt>Sliding window</dt>
            <dd>{fmtCount(attn.slidingWindow)}</dd>
          </>
        )}
        {attn?.ropeTheta != null && (
          <>
            <dt>RoPE θ</dt>
            <dd>{attn.ropeTheta.toLocaleString()}</dd>
          </>
        )}
        {arch.hiddenSize != null && (
          <>
            <dt>d_model</dt>
            <dd>{arch.hiddenSize}</dd>
          </>
        )}
      </dl>

      {attn?.headDimDerived && (
        <div className="interp-dim md-caveat">
          ~ head dim is inferred from d_model ÷ heads; this config doesn&apos;t state it, and
          some models (Gemma 3) set it independently.
        </div>
      )}

      {arch.notes.map((note) => (
        <div key={note} className="interp-warn md-note">
          {note}
        </div>
      ))}
    </div>
  );
}
