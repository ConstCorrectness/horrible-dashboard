/**
 * The architecture as a graph, derived from what the model actually is.
 *
 * ## Why this is not 48 blocks
 *
 * A 48-layer model has ~290 sublayers, and drawing them all would be unreadable
 * and also a lie about what is interesting. The interesting fact about a stack of
 * 48 decoder blocks is that they are **identical** — 48 drawn boxes assert the
 * opposite. So the stack is one node carrying a *rail* of N ticks, and exactly one
 * block is ever expanded: the selected one. The node count is O(1) in the layer
 * count, which is also what stops the canvas rebuilding 290 nodes on every poll.
 *
 * ## Where blocks are not identical
 *
 * Some models alternate their attention shape by layer — Gemma 4's `blk.0.attn_q`
 * is 3840x4096 while `blk.17.attn_q` is 3840x8192. The pane used to say this with
 * a grey `varies` chip and a tooltip. `layerSignatures` groups blocks by the shapes
 * of their own tensors, so the rail is drawn in bands and the alternation is
 * visible rather than described. The GGUF inventory is the only source that can see
 * it: every scalar source — GGUF metadata, a repo's config.json — reports a single
 * head count for the whole model.
 *
 * ## Selection stays where it was
 *
 * `Selection {stage, layer}` remains the single source of truth. This module only
 * provides a bijection to node ids, so the canvas is a *projection* of the
 * selection rather than a second copy of it. That is what lets the model-locus
 * effect (lens -> explorer) keep working with no change at all.
 */
import type { AttentionSpec, ModelArchitecture, ModelTensors, TensorEntry } from '../store';

export interface Selection {
  stage: string;
  layer: number | null;
}

export type InspectNodeKind = 'io' | 'stack' | 'norm' | 'attn' | 'ffn' | 'moe';

export interface InspectNode {
  id: string;
  kind: InspectNodeKind;
  label: string;
  sub?: string;
  /** Short measured facts, drawn only above a zoom threshold. */
  facts: string[];
  selection: Selection;
  /** The stack node only. */
  rail?: LayerRail;
  attention?: AttentionSpec;
}

export interface InspectEdge {
  id: string;
  source: string;
  target: string;
  /**
   * A skip connection, drawn to the side. The residual is the one piece of the
   * architecture the old DOM stack could not draw at all — it rendered
   * "(+) residual" as a text row that connected nothing to nothing.
   */
  residual?: boolean;
}

export interface LayerRail {
  count: number;
  /** Per block: relative size 0..1, and which shape-signature band it belongs to. */
  ticks: { index: number; weight: number; band: number }[];
  bands: number;
  selected: number | null;
}

export interface InspectGraph {
  nodes: InspectNode[];
  edges: InspectEdge[];
  /** The node the current selection maps to, for centring the viewport. */
  focusId: string | null;
}

export const NODE_IDS = {
  model: 'model',
  embedding: 'embedding',
  stack: 'stack',
  output: 'output',
} as const;

/* ---------------------------------------------------------------- selection -- */

/** The node a selection lands on. */
export function nodeIdFor(selection: Selection): string {
  switch (selection.stage) {
    case 'model':
      return NODE_IDS.model;
    case 'embedding':
      return NODE_IDS.embedding;
    case 'output':
      return NODE_IDS.output;
    case 'block':
      return NODE_IDS.stack;
    default:
      // The sublayers of the expanded block. The layer is deliberately NOT part of
      // the id: only one block is expanded at a time, so `attention` is
      // unambiguous, and keeping the layer out means the id is stable while
      // stepping through layers — which is what stops React Flow tearing the node
      // down and rebuilding it on every step.
      return selection.stage;
  }
}

/** The selection a node id means, at the layer currently open. */
export function selectionFor(nodeId: string, layer: number | null): Selection {
  switch (nodeId) {
    case NODE_IDS.model:
      return { stage: 'model', layer: null };
    case NODE_IDS.embedding:
      return { stage: 'embedding', layer: null };
    case NODE_IDS.output:
      return { stage: 'output', layer: null };
    case NODE_IDS.stack:
      return { stage: 'block', layer };
    default:
      return { stage: nodeId, layer };
  }
}

/* ------------------------------------------------------------------- shapes -- */

/**
 * Blocks grouped by the shapes of their own tensors.
 *
 * The role name is the tensor name with its `blk.N.` prefix removed, so
 * `blk.17.attn_q.weight` and `blk.0.attn_q.weight` are the same role and their
 * shapes are comparable. A model whose blocks are all alike yields one band.
 */
export function layerSignatures(tensors: TensorEntry[]): { bands: number; bandOf: number[] } {
  const perLayer = new Map<number, string[]>();
  for (const t of tensors) {
    if (t.layer === null) continue;
    const role = t.name.replace(/(?:^|\.)blk\.\d+\./, '');
    const list = perLayer.get(t.layer) ?? [];
    list.push(`${role}:${t.shape.join('x')}`);
    perLayer.set(t.layer, list);
  }
  if (perLayer.size === 0) return { bands: 0, bandOf: [] };

  const seen = new Map<string, number>();
  const bandOf: number[] = [];
  for (const index of [...perLayer.keys()].sort((a, b) => a - b)) {
    // Sorted, because tensor order within a block is an artefact of the file's
    // directory rather than a property of the block. Two identical blocks listed
    // in a different order must not read as two different shapes.
    const signature = (perLayer.get(index) ?? []).sort().join('|');
    let band = seen.get(signature);
    if (band === undefined) {
      band = seen.size;
      seen.set(signature, band);
    }
    bandOf[index] = band;
  }
  return { bands: seen.size, bandOf };
}

/** Total bytes per block, for the rail's tick heights. */
function bytesPerLayer(tensors: TensorEntry[], count: number): number[] {
  const out = new Array<number>(count).fill(0);
  for (const t of tensors) {
    if (t.layer === null || t.layer < 0 || t.layer >= count) continue;
    out[t.layer] += t.byteSize ?? 0;
  }
  return out;
}

/**
 * The query/KV head layout, capped.
 *
 * Extracted from the render so the caps are a tested decision rather than two
 * magic numbers. Eight is where the pattern is established; past it the ratio
 * label carries the rest, and 64 ticks 2px apart say nothing more.
 */
export const HEAD_GROUP_CAP = 8;

export function headGroups(
  attention: AttentionSpec,
): { groups: number; perGroup: number; hidden: number } | null {
  const kv = attention.kvHeads;
  const ratio = attention.groupRatio;
  if (!kv || !ratio) return null;
  const groups = Math.min(kv, HEAD_GROUP_CAP);
  return {
    groups,
    perGroup: Math.min(ratio, HEAD_GROUP_CAP),
    hidden: Math.max(0, kv - groups),
  };
}

/* -------------------------------------------------------------------- build -- */

function fmtCount(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

const ATTENTION_LABEL: Record<string, string> = {
  mha: 'Multi-head attention',
  gqa: 'Grouped-query attention',
  mqa: 'Multi-query attention',
  unknown: 'Attention',
};

export function buildInspectGraph(
  arch: ModelArchitecture,
  inventory: ModelTensors | null,
  selection: Selection,
  stackOpen: boolean,
): InspectGraph {
  const nodes: InspectNode[] = [];
  const edges: InspectEdge[] = [];
  const count = arch.layers ?? inventory?.layerCount ?? 0;
  const tensors = inventory?.tensors ?? [];

  const { bands, bandOf } = layerSignatures(tensors);
  const bytes = bytesPerLayer(tensors, count);
  const heaviest = Math.max(1, ...bytes);

  nodes.push({
    id: NODE_IDS.model,
    kind: 'io',
    label: arch.model || 'Model',
    sub: `${fmtCount(arch.parameterCount ?? inventory?.totalParameters ?? null)} params`,
    facts: [
      arch.family || '',
      inventory ? `${inventory.tensorCount} tensors` : '',
      arch.contextLength ? `${arch.contextLength.toLocaleString()} ctx` : '',
    ].filter(Boolean),
    selection: { stage: 'model', layer: null },
  });

  nodes.push({
    id: NODE_IDS.embedding,
    kind: 'io',
    label: 'Token embedding',
    sub: `${fmtCount(arch.vocabSize)} × ${arch.hiddenSize ?? '—'}`,
    facts: [],
    selection: { stage: 'embedding', layer: null },
  });

  nodes.push({
    id: NODE_IDS.stack,
    kind: 'stack',
    label: 'Decoder block',
    sub: `× ${count || '?'}`,
    facts: bands > 1 ? [`${bands} distinct block shapes`] : [],
    selection: { stage: 'block', layer: selection.layer },
    rail: {
      count,
      ticks: Array.from({ length: count }, (_, i) => ({
        index: i,
        // A floor, so a block whose bytes we could not size is still a tick rather
        // than a gap: the rail is an index as well as a chart, and a missing tick
        // would shift every label after it.
        weight: bytes[i] ? Math.max(0.08, bytes[i] / heaviest) : 0.35,
        band: bandOf[i] ?? 0,
      })),
      bands,
      selected: selection.layer,
    },
  });

  nodes.push({
    id: NODE_IDS.output,
    kind: 'io',
    label: 'Output head',
    sub:
      arch.tiedEmbeddings === true ? 'tied to embedding' : `→ ${fmtCount(arch.vocabSize)} logits`,
    facts: [],
    selection: { stage: 'output', layer: null },
  });

  edges.push({ id: 'e-model', source: NODE_IDS.model, target: NODE_IDS.embedding });
  edges.push({ id: 'e-emb', source: NODE_IDS.embedding, target: NODE_IDS.stack });
  edges.push({ id: 'e-out', source: NODE_IDS.stack, target: NODE_IDS.output });

  // The inside of ONE block. Six to eight nodes, ever.
  if (stackOpen) {
    const inner: string[] = [];
    const push = (node: InspectNode) => {
      nodes.push(node);
      inner.push(node.id);
    };
    const layerTensors = tensors.filter((t) => t.layer === selection.layer);
    const factsFor = (component: string) => {
      const list = layerTensors.filter((t) => t.component === component);
      if (list.length === 0) return [];
      const total = list.reduce((sum, t) => sum + (t.byteSize ?? 0), 0);
      return [`${list.length} tensors`, `${(total / 1024 ** 2).toFixed(1)} MB`];
    };

    if (arch.normType) {
      push({
        id: 'norm',
        kind: 'norm',
        label: arch.normType === 'rmsnorm' ? 'RMSNorm' : arch.normType,
        facts: factsFor('norm'),
        selection: { stage: 'norm', layer: selection.layer },
      });
    }
    if (arch.attention) {
      const attn = arch.attention;
      push({
        id: 'attention',
        kind: 'attn',
        label: ATTENTION_LABEL[attn.kind] ?? 'Attention',
        sub:
          `${attn.heads ?? '—'} heads` +
          (attn.kvHeads != null && attn.kvHeads !== attn.heads ? ` / ${attn.kvHeads} KV` : ''),
        facts: [
          attn.headDim ? `dim ${attn.headDimDerived ? '~' : ''}${attn.headDim}` : '',
          attn.slidingWindow ? `window ${attn.slidingWindow}` : '',
          ...factsFor('attention'),
        ].filter(Boolean),
        selection: { stage: 'attention', layer: selection.layer },
        attention: attn,
      });
    }
    if (arch.moe) {
      push({
        id: 'moe',
        kind: 'moe',
        label: 'Mixture of experts',
        sub: `top-${arch.moe.expertsPerToken} of ${arch.moe.experts}`,
        facts: [
          arch.moe.activeFraction != null
            ? `${Math.round(arch.moe.activeFraction * 100)}% active`
            : '',
          ...factsFor('moe'),
        ].filter(Boolean),
        selection: { stage: 'moe', layer: selection.layer },
      });
    } else {
      push({
        id: 'ffn',
        kind: 'ffn',
        label: 'Feed-forward',
        sub: arch.ffn?.intermediateSize
          ? `${arch.hiddenSize ?? '—'} → ${arch.ffn.intermediateSize}`
          : undefined,
        facts: [arch.ffn?.gated === true ? 'gated' : '', ...factsFor('ffn')].filter(Boolean),
        selection: { stage: 'ffn', layer: selection.layer },
      });
    }

    // The block's own chain, then the skip that makes it a residual stream. The
    // skip edge is the entire argument for drawing this as a graph: it is a
    // connection, and a vertical list of rows cannot draw a connection.
    for (let i = 0; i < inner.length - 1; i += 1) {
      edges.push({ id: `e-in-${i}`, source: inner[i], target: inner[i + 1] });
    }
    if (inner.length > 0) {
      edges.push({ id: 'e-block-in', source: NODE_IDS.stack, target: inner[0] });
      edges.push({
        id: 'e-residual',
        source: NODE_IDS.stack,
        target: inner[inner.length - 1],
        residual: true,
      });
    }
  }

  const focusId = nodeIdFor(selection);
  return { nodes, edges, focusId: nodes.some((n) => n.id === focusId) ? focusId : null };
}

/**
 * A key that changes exactly when the drawing would.
 *
 * The interpretability store refetches on a schedule, and an identical refetch
 * produces a new object every time. Memoizing on object identity would rebuild
 * every node on every poll — and React Flow, handed new node objects, discards the
 * viewport mid-pan. Keyed on the values that are actually drawn, an identical
 * refetch is a no-op.
 */
export function inspectGraphKey(
  arch: ModelArchitecture | null,
  inventory: ModelTensors | null,
  selection: Selection,
  stackOpen: boolean,
): string {
  return [
    arch?.model ?? '',
    arch?.layers ?? '',
    arch?.hiddenSize ?? '',
    arch?.vocabSize ?? '',
    arch?.normType ?? '',
    arch?.attention?.kind ?? '',
    arch?.moe?.experts ?? '',
    inventory?.tensorCount ?? '',
    selection.stage,
    selection.layer ?? '',
    stackOpen ? '1' : '0',
  ].join('|');
}
