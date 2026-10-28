/**
 * The Attention section's arithmetic, kept out of the component so it is tested
 * rather than eyeballed: what each mixer kind is called and coloured, which cells
 * of a small attention mask a kind allows, and how bytes and reach read.
 *
 * Nothing here decides *what* a layer is — that is the backend's
 * `attention_layout.py`, from the GGUF header. This only draws it.
 */
import type { AttentionLayout, LayerAttention } from '../../interpretability/store';

export interface KindMeta {
  /** Long name, for the selected-layer card. */
  label: string;
  /** Legend / chip text. */
  short: string;
  /** A theme token expression — every theme defines these. */
  color: string;
  /** One sentence on what the kind does to a token's view of the past. */
  gloss: string;
}

export const KIND_META: Record<string, KindMeta> = {
  global: {
    label: 'Global attention',
    short: 'global',
    color: 'var(--accent)',
    gloss: 'Every query sees every earlier position — the full causal triangle.',
  },
  sliding: {
    label: 'Sliding-window attention',
    short: 'sliding',
    color: 'color-mix(in srgb, var(--accent) 42%, var(--bg-secondary))',
    gloss: 'Each query sees only the last `window` positions, so its KV cache stops growing there.',
  },
  chunked: {
    label: 'Chunked attention',
    short: 'chunked',
    color: 'var(--warn)',
    gloss: 'Queries attend only within their fixed-size chunk; nothing crosses a chunk boundary.',
  },
  mla: {
    label: 'Multi-head latent attention',
    short: 'MLA',
    color: 'color-mix(in srgb, var(--accent) 70%, var(--success))',
    gloss:
      'Full attention over a compressed latent KV, cached instead of per-head keys and values.',
  },
  linear: {
    label: 'Linear attention (recurrent state)',
    short: 'linear',
    color: 'var(--success)',
    gloss:
      'No attention matrix: a fixed-size state is updated token by token (Gated DeltaNet, RWKV).',
  },
  ssm: {
    label: 'State-space block (Mamba)',
    short: 'SSM',
    color: 'color-mix(in srgb, var(--success) 55%, var(--bg-secondary))',
    gloss: 'A selective state-space scan — a recurrent state, not a cache of past keys.',
  },
  conv: {
    label: 'Short convolution',
    short: 'conv',
    color: 'var(--danger)',
    gloss: 'A causal convolution over the last few positions; no attention at all.',
  },
  none: {
    label: 'No token mixer',
    short: 'none',
    color: 'var(--text-faint)',
    gloss: 'This block mixes nothing across positions.',
  },
};

export function kindMeta(kind: string): KindMeta {
  return KIND_META[kind] ?? KIND_META.none;
}

/** Kinds in the order the legend lists them, filtered to those present. */
export function kindsPresent(layout: AttentionLayout): string[] {
  return Object.keys(KIND_META).filter((k) => (layout.counts[k] ?? 0) > 0);
}

/**
 * Which cells of an `n`×`n` causal mask a kind allows, scaled so the thumbnail
 * reads: a window or chunk is drawn as a fraction of `n` rather than at its real
 * size, which would be a full triangle at any thumbnail resolution.
 *
 * `null` for kinds with no mask at all (recurrent states, convolutions are drawn
 * as a band of fixed width).
 */
export function maskCells(kind: string, n: number): boolean[][] | null {
  const band = Math.max(2, Math.round(n / 3));
  const cells = (allow: (q: number, k: number) => boolean) =>
    Array.from({ length: n }, (_, q) => Array.from({ length: n }, (_, k) => k <= q && allow(q, k)));
  switch (kind) {
    case 'global':
    case 'mla':
      return cells(() => true);
    case 'sliding':
      return cells((q, k) => q - k < band);
    case 'chunked':
      return cells((q, k) => Math.floor(q / band) === Math.floor(k / band));
    case 'conv':
      return cells((q, k) => q - k < 3);
    default:
      return null;
  }
}

export function fmtBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(n >= 10 * 1024 ** 3 ? 1 : 2)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(n >= 100 * 1024 ** 2 ? 0 : 1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

/**
 * "128k". Decimal first: 128000 is also exactly 125 × 1024, and a model that
 * states a 128000 context means 128k, not 125k. Binary sizes (131072) are not
 * round decimals, so they still read as their binary k.
 */
export function fmtContext(n: number): string {
  if (n >= 1000 && n % 1000 === 0) return `${n / 1000}k`;
  if (n >= 1024 && n % 1024 === 0) return `${n / 1024}k`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

export function fmtReach(reach: number | null): string {
  return reach === null ? 'whole context' : `≤ ${reach.toLocaleString()} tokens`;
}

/** "13", or null when the layer keeps its own cache. */
export function sharedFrom(layer: LayerAttention): string | null {
  return layer.kvSourceLayer === null ? null : `L${layer.kvSourceLayer}`;
}

/**
 * The KV-sharing arcs to draw: one per layer that reads another's cache, from
 * source to reader. Grouped so a run of readers of one source draws as one fan.
 */
export function shareArcs(layers: LayerAttention[]): { from: number; to: number }[] {
  return layers
    .filter((l) => l.kvSourceLayer !== null)
    .map((l) => ({ from: l.kvSourceLayer as number, to: l.layer }));
}

/**
 * Two spellings of one weights file: separators and drive-letter case differ
 * between the header reader and a trace manifest. The layout describes the agent's
 * loaded model and a trace can be of any GGUF, so a layer kind may be shown beside
 * a trace's heads only when this holds.
 */
export function sameFile(a: string | null | undefined, b: string | null | undefined): boolean {
  const norm = (p: string) => p.replace(/\\/g, '/').toLowerCase();
  return Boolean(a && b) && norm(a as string) === norm(b as string);
}

/** How many times smaller the real cache is than an all-global one, at a context. */
export function savings(point: { bytes: number; bytesIfGlobal: number }): number | null {
  if (!point.bytes || !point.bytesIfGlobal) return null;
  return point.bytesIfGlobal / point.bytes;
}
