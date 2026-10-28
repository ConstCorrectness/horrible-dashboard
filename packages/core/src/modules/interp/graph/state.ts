/**
 * The Graph section's state, outside any component: the graphs on this node, the
 * one open, the reader's pins and supernodes, the pruning, and the selection.
 *
 * Pins and supernodes are written back into the graph's own `qParams` (debounced),
 * so they survive a reload and travel with the file.
 */
import { useSyncExternalStore } from 'react';

import { getFeature, type FeatureCard } from '../library/api';
import { getGraph, importGraph, listGraphs, saveQParams, type GraphArtifact } from './api';
import {
  describeNode,
  featureIndex,
  kindOf,
  prune,
  type AttributionGraph,
  type GraphNode,
} from './model';

export interface GraphState {
  graphs: GraphArtifact[];
  id: string | null;
  artifact: GraphArtifact | null;
  graph: AttributionGraph | null;
  /** Cumulative-influence cutoff for features (smaller influence = more important). */
  nodeThreshold: number;
  /** Keep an edge carrying at least this share of its target's strongest input. */
  edgeFraction: number;
  pinnedOnly: boolean;
  pinned: string[];
  supernodes: string[][];
  /** Nodes gathered for a new supernode (shift-click). */
  grouping: string[];
  selected: string | null;
  /** Neuronpedia cards by node id, for transcoder features. */
  cards: Record<string, FeatureCard>;
  loading: boolean;
  error: string | null;
}

let state: GraphState = {
  graphs: [],
  id: null,
  artifact: null,
  graph: null,
  nodeThreshold: 0.6,
  edgeFraction: 0.1,
  pinnedOnly: false,
  pinned: [],
  supernodes: [],
  grouping: [],
  selected: null,
  cards: {},
  loading: false,
  error: null,
};
const listeners = new Set<() => void>();

function set(patch: Partial<GraphState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export const graphStore = {
  get: (): GraphState => state,
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useGraph(): GraphState {
  return useSyncExternalStore(graphStore.subscribe, graphStore.get);
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export async function refreshGraphs(): Promise<void> {
  try {
    const { graphs } = await listGraphs();
    set({ graphs });
    if (!state.id && graphs.length) await openGraph(graphs[0].id);
  } catch (err) {
    set({ error: message(err) });
  }
}

export async function openGraph(id: string): Promise<void> {
  set({
    id,
    graph: null,
    artifact: null,
    selected: null,
    grouping: [],
    cards: {},
    loading: true,
    error: null,
  });
  try {
    const r = await getGraph(id);
    if (state.id !== id) return;
    if (r.error || !r.graph) {
      set({ loading: false, error: r.error ?? 'the graph did not load' });
      return;
    }
    const q = r.graph.qParams ?? {};
    set({
      graph: r.graph,
      artifact: r.artifact ?? null,
      pinned: [...(q.pinnedIds ?? [])],
      supernodes: (q.supernodes ?? []).map((g) => [...g]),
      loading: false,
    });
  } catch (err) {
    set({ loading: false, error: message(err) });
  }
}

export async function importFrom(body: {
  ref?: string;
  json_text?: string;
  name?: string;
}): Promise<string | null> {
  set({ loading: true, error: null });
  try {
    const r = await importGraph(body);
    if (r.error || !r.graph) {
      set({ loading: false, error: r.error ?? 'the import failed' });
      return r.error ?? 'the import failed';
    }
    await refreshGraphs();
    await openGraph(r.graph.id);
    return null;
  } catch (err) {
    set({ loading: false, error: message(err) });
    return message(err);
  }
}

export function setPruning(
  patch: Partial<Pick<GraphState, 'nodeThreshold' | 'edgeFraction' | 'pinnedOnly'>>,
): void {
  set(patch);
}

// ── annotations, persisted ───────────────────────────────────────────────────

let saveTimer: ReturnType<typeof setTimeout> | null = null;

function persist(): void {
  const id = state.id;
  if (!id) return;
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    void saveQParams(id, { pinnedIds: state.pinned, supernodes: state.supernodes }).catch(
      (err: unknown) => set({ error: `pins not saved: ${message(err)}` }),
    );
  }, 600);
}

export function togglePin(nodeId: string): void {
  const pinned = state.pinned.includes(nodeId)
    ? state.pinned.filter((p) => p !== nodeId)
    : [...state.pinned, nodeId];
  set({ pinned });
  persist();
}

export function toggleGrouping(nodeId: string): void {
  set({
    grouping: state.grouping.includes(nodeId)
      ? state.grouping.filter((p) => p !== nodeId)
      : [...state.grouping, nodeId],
  });
}

/** Make a supernode of the gathered nodes. Its members are pinned too. */
export function groupAs(label: string): void {
  const members = state.grouping;
  if (!label.trim() || members.length < 2) return;
  set({
    supernodes: [...state.supernodes, [label.trim(), ...members]],
    pinned: [...new Set([...state.pinned, ...members])],
    grouping: [],
  });
  persist();
}

export function ungroup(index: number): void {
  set({ supernodes: state.supernodes.filter((_, i) => i !== index) });
  persist();
}

// ── selection and labels ─────────────────────────────────────────────────────

export function select(nodeId: string | null): void {
  set({ selected: nodeId });
  const node = nodeId ? state.graph?.nodes.find((n) => n.node_id === nodeId) : null;
  if (node) void loadCard(node);
}

/** Neuronpedia's card for a transcoder feature, when the graph names its source set. */
export async function loadCard(node: GraphNode): Promise<FeatureCard | null> {
  const model = state.graph?.metadata.scan;
  const sourceSet = state.artifact?.facts.sourceSet;
  if (!model || !sourceSet || node.feature === undefined || kindOf(node) !== 'feature') {
    return null;
  }
  if (state.cards[node.node_id]) return state.cards[node.node_id];
  try {
    const card = await getFeature(model, `${node.layer}-${sourceSet}`, String(featureIndex(node)));
    set({ cards: { ...state.cards, [node.node_id]: card } });
    return card;
  } catch {
    return null;
  }
}

/** A node's label: its own `clerp`, else Neuronpedia's explanation once loaded. */
export function labelOf(
  node: GraphNode,
  cards: Record<string, FeatureCard> = state.cards,
): string | null {
  return node.clerp || cards[node.node_id]?.explanations?.[0]?.text || null;
}

// ── the digest ───────────────────────────────────────────────────────────────

export function graphDigest(current: GraphState = state): string | null {
  const g = current.graph;
  if (!g) return null;
  const pruned = prune(
    g,
    current.nodeThreshold,
    current.edgeFraction,
    new Set(current.pinned),
    current.pinnedOnly,
  );
  const target =
    g.nodes.find((n) => n.is_target_logit) ??
    g.nodes
      .filter((n) => n.feature_type === 'logit')
      .sort((a, b) => (b.token_prob ?? 0) - (a.token_prob ?? 0))[0];
  return (
    `graph ${g.metadata.slug ?? current.id} (${g.metadata.scan ?? 'model?'}): ` +
    `${pruned.nodes.length}/${g.nodes.length} nodes shown` +
    (target ? `, target ${describeNode(target, g.metadata.prompt_tokens)}` : '') +
    (current.pinned.length ? `; ${current.pinned.length} pinned` : '') +
    (current.supernodes.length
      ? `; supernodes ${current.supernodes.map((s) => s[0]).join(', ')}`
      : '')
  );
}
