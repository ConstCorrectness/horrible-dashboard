/**
 * An attribution graph as data: its shape (circuit-tracer / Neuronpedia JSON), the
 * client-side pruning that keeps it readable, and the layout the canvas draws.
 * Pure — no DOM — so the parts that decide what is shown are tested directly.
 */

export interface GraphNode {
  node_id: string;
  /** `"E"` for embeddings; the logit layer is one past the last block. */
  layer: string | number;
  ctx_idx: number;
  feature_type: string;
  feature?: number;
  clerp?: string;
  influence?: number | null;
  activation?: number | null;
  token_prob?: number;
  is_target_logit?: boolean;
}

export interface GraphLink {
  source: string;
  target: string;
  weight: number;
}

export interface AttributionGraph {
  metadata: {
    slug?: string;
    scan?: string;
    prompt?: string;
    prompt_tokens: string[];
    [key: string]: unknown;
  };
  qParams?: { pinnedIds?: string[]; supernodes?: string[][]; [key: string]: unknown };
  nodes: GraphNode[];
  links: GraphLink[];
}

export type NodeKind = 'embedding' | 'feature' | 'error' | 'logit';

export function kindOf(node: GraphNode): NodeKind {
  const t = node.feature_type;
  if (t === 'embedding') return 'embedding';
  if (t === 'logit') return 'logit';
  if (t.includes('error')) return 'error';
  return 'feature';
}

/** Layers bottom-up: embeddings, blocks in order, logits. */
export function layerRank(node: GraphNode): number {
  if (node.layer === 'E') return -1;
  return Number(node.layer);
}

export interface Pruned {
  nodes: GraphNode[];
  links: GraphLink[];
  hiddenNodes: number;
  hiddenLinks: number;
}

/**
 * Keep what explains the prediction.
 *
 * Nodes: embeddings and logits always; features and error nodes whose
 * `influence` — circuit-tracer's cumulative influence, smaller is more
 * important — is at or under `nodeThreshold` (no influence recorded: kept).
 * Links: among kept nodes, those carrying at least `edgeFraction` of the largest
 * |weight| into the same target, so a node's few strong inputs survive however
 * weak its neighbourhood is. `pinnedOnly` keeps the reader's subgraph (plus the
 * two ends) instead.
 */
export function prune(
  graph: AttributionGraph,
  nodeThreshold: number,
  edgeFraction: number,
  pinned: ReadonlySet<string> = new Set(),
  pinnedOnly = false,
): Pruned {
  const keep = new Set<string>();
  for (const n of graph.nodes) {
    const kind = kindOf(n);
    if (kind === 'embedding' || kind === 'logit') keep.add(n.node_id);
    else if (pinnedOnly) {
      if (pinned.has(n.node_id)) keep.add(n.node_id);
    } else if (n.influence == null || n.influence <= nodeThreshold || pinned.has(n.node_id)) {
      keep.add(n.node_id);
    }
  }
  const maxInto = new Map<string, number>();
  for (const l of graph.links) {
    if (!keep.has(l.source) || !keep.has(l.target)) continue;
    maxInto.set(l.target, Math.max(maxInto.get(l.target) ?? 0, Math.abs(l.weight)));
  }
  const links = graph.links.filter(
    (l) =>
      keep.has(l.source) &&
      keep.has(l.target) &&
      Math.abs(l.weight) >= edgeFraction * (maxInto.get(l.target) ?? 0),
  );
  // In pinned-only view, embeddings and logits with no edge left are noise.
  let nodes = graph.nodes.filter((n) => keep.has(n.node_id));
  if (pinnedOnly) {
    const touched = new Set(links.flatMap((l) => [l.source, l.target]));
    nodes = nodes.filter(
      (n) => kindOf(n) === 'feature' || kindOf(n) === 'error' || touched.has(n.node_id),
    );
  }
  return {
    nodes,
    links,
    hiddenNodes: graph.nodes.length - nodes.length,
    hiddenLinks: graph.links.length - links.length,
  };
}

export interface Placed {
  node: GraphNode;
  x: number;
  y: number;
}

/**
 * Token positions left to right, layers bottom to top (embeddings at the bottom,
 * logits at the top), nodes sharing a (layer, position) cell side by side.
 * Only layers that have a node get a row: a 26-block model whose circuit lives in
 * eight layers should not be mostly empty rows.
 */
export function layout(
  nodes: readonly GraphNode[],
  tokens: number,
  width: number,
  height: number,
  pad = 28,
): { placed: Placed[]; rows: number[] } {
  const rows = [...new Set(nodes.map(layerRank))].sort((a, b) => a - b);
  const rowY = new Map(
    rows.map((r, i) => [r, height - pad - (i * (height - 2 * pad)) / Math.max(1, rows.length - 1)]),
  );
  const colW = (width - 2 * pad) / Math.max(1, tokens);
  const cells = new Map<string, GraphNode[]>();
  for (const n of nodes) {
    const key = `${layerRank(n)}:${n.ctx_idx}`;
    const list = cells.get(key) ?? [];
    list.push(n);
    cells.set(key, list);
  }
  const placed: Placed[] = [];
  for (const list of cells.values()) {
    list.sort((a, b) => (a.influence ?? 0) - (b.influence ?? 0));
    list.forEach((node, i) => {
      const spread = Math.min(colW * 0.8, list.length * 9);
      const offset = list.length === 1 ? 0 : (i / (list.length - 1) - 0.5) * spread;
      placed.push({
        node,
        x: pad + colW * (node.ctx_idx + 0.5) + offset,
        y: rowY.get(layerRank(node)) ?? pad,
      });
    });
  }
  return { placed, rows };
}

/** A node's strongest edges one way, by |weight|. */
export function topEdges(
  links: readonly GraphLink[],
  nodeId: string,
  direction: 'in' | 'out',
  k = 8,
): GraphLink[] {
  return links
    .filter((l) => (direction === 'in' ? l.target === nodeId : l.source === nodeId))
    .sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight))
    .slice(0, k);
}

/**
 * A transcoder node's feature index within its layer: the id's middle part
 * (`20_15589_10` → 15589). Some generators store an encoded `layer * 100000 +
 * index` in `feature`; looking that number up on Neuronpedia finds nothing.
 */
export function featureIndex(node: GraphNode): number | undefined {
  const parts = node.node_id.split('_');
  if (parts.length === 3 && parts[0] !== 'E') {
    const n = Number(parts[1]);
    if (Number.isFinite(n)) return n;
  }
  return node.feature;
}

/** A node in words: `L15 F77 at " is"`, `Emb " Dallas"`, the logit's own label. */
export function describeNode(node: GraphNode, tokens: readonly string[]): string {
  const tok = tokens[node.ctx_idx] ?? '?';
  switch (kindOf(node)) {
    case 'embedding':
      return `Emb ${JSON.stringify(tok)}`;
    case 'logit':
      return node.clerp || `logit ${node.feature}`;
    case 'error':
      return `error L${node.layer} at ${JSON.stringify(tok)}`;
    default:
      return `L${node.layer} F${featureIndex(node)} at ${JSON.stringify(tok)}`;
  }
}
