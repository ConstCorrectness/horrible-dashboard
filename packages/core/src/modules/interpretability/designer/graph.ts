/**
 * The design graph on the client: types mirroring the backend's Pydantic models,
 * and the calls that turn a graph into shapes and into PyTorch.
 *
 * The node **catalog is fetched, never duplicated here**. Two copies of a
 * vocabulary drift, and the drift is silent — a node keeps offering a parameter the
 * generator stopped emitting, and the only symptom is a model that trains slightly
 * differently than the one you drew. Same reason hassault serves its weapon numbers
 * and its `plane_order` instead of restating them in TypeScript.
 *
 * See docs/modules/interpretability.mdx.
 */
import { apiDelete, apiGet, apiPost, apiPut } from '../../../api';

/** A concrete size, or a symbol (`B`, `T`) only known at run time. */
export type Dim = number | string;
export type Shape = Dim[];

export interface GraphNode {
  id: string;
  type: string;
  params: Record<string, unknown>;
  /** Blender's node mute — here, an ablation. The node emits nothing. */
  muted?: boolean;
  /** Overrides the generated `self.<name>` attribute. */
  name?: string;
}

export interface GraphEdge {
  id?: string;
  source: string;
  sourceHandle?: string | null;
  target: string;
  targetHandle?: string | null;
}

/** A node group — one generated `nn.Module` subclass. */
export interface SubGraph {
  id: string;
  name: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface DesignGraph {
  name: string;
  /** Becomes the root class's `__init__` keyword arguments; `"$d_model"` reads it. */
  config: Record<string, number | string | boolean>;
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: SubGraph[];
}

export interface SocketSpec {
  name: string;
  type: 'tensor' | 'int' | 'float' | 'bool';
  multi: boolean;
  label: string;
}

export interface ParamSpec {
  name: string;
  label: string;
  type: 'int' | 'float' | 'bool' | 'text' | 'select';
  default: unknown;
  help: string;
  options: string[];
}

export interface NodeSpec {
  type: string;
  label: string;
  category: string;
  doc: string;
  inputs: SocketSpec[];
  outputs: SocketSpec[];
  params: ParamSpec[];
}

export interface TemplateSpec {
  id: string;
  label: string;
  description: string;
}

export interface Catalog {
  nodes: NodeSpec[];
  templates: TemplateSpec[];
}

export interface ShapeIssue {
  nodeId: string;
  handle: string;
  edgeId: string;
  severity: 'error' | 'warning';
  message: string;
}

export interface ShapeReport {
  ok: boolean;
  shapes: Record<string, Record<string, Shape>>;
  params: Record<string, number>;
  totalParams: number;
  issues: ShapeIssue[];
}

export interface CodeResult {
  source: string;
  markers: Record<string, string>;
  error: string | null;
}

export interface NodeLayout {
  x: number;
  y: number;
  collapsed?: boolean;
  label?: string;
  frame?: string;
}

export interface Layout {
  nodes: Record<string, NodeLayout>;
  frames: { id: string; label: string; color: string }[];
  viewport: Record<string, number>;
}

export interface StoredDesign {
  name: string;
  graph: DesignGraph;
  layout: Layout;
  source: string;
  codeError: string | null;
}

export interface DesignSummary {
  name: string;
  modified: number;
  bytes: number;
}

const BASE = '/interpretability/graph';

export function getCatalog(): Promise<Catalog> {
  return apiGet<Catalog>(`${BASE}/specs`);
}

export function getTemplate(id: string): Promise<DesignGraph> {
  return apiGet<DesignGraph>(`${BASE}/template/${encodeURIComponent(id)}`);
}

/**
 * Graph → Python. Stateless on purpose: the code pane re-runs this on every edit,
 * long before the design has a name or has been saved.
 */
export function toCode(graph: DesignGraph): Promise<CodeResult> {
  return apiPost<CodeResult>(`${BASE}/code`, graph);
}

/** Tier-1 shape inference — ours, not torch's, and labelled as such in the pane. */
export function validateGraph(graph: DesignGraph): Promise<ShapeReport> {
  return apiPost<ShapeReport>(`${BASE}/validate`, graph);
}

export function listDesigns(): Promise<{ designs: DesignSummary[] }> {
  return apiGet<{ designs: DesignSummary[] }>(BASE);
}

export function loadDesign(name: string): Promise<StoredDesign> {
  return apiGet<StoredDesign>(`${BASE}/${encodeURIComponent(name)}`);
}

export function saveDesign(
  name: string,
  graph: DesignGraph,
  layout?: Layout,
): Promise<StoredDesign> {
  return apiPut<StoredDesign>(`${BASE}/${encodeURIComponent(name)}`, { graph, layout });
}

export function deleteDesign(name: string): Promise<{ deleted: boolean }> {
  return apiDelete<{ deleted: boolean }>(`${BASE}/${encodeURIComponent(name)}`);
}

/** `[B, T, 2048]` — how a wire labels itself. */
export function formatShape(shape: Shape | undefined): string {
  return shape?.length ? `[${shape.join(', ')}]` : '';
}

export function formatCount(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

let seq = 0;
/** Unique across the whole design, including inside groups — the backend keys
 * shapes, parameter counts and code markers by node id with no scope prefix. */
export function newNodeId(type: string): string {
  seq += 1;
  return `${type.split('.').pop()}_${Date.now().toString(36)}${seq}`;
}

/** A node carrying every default its spec declares, so nothing is `undefined`. */
export function nodeFromSpec(spec: NodeSpec): GraphNode {
  const params: Record<string, unknown> = {};
  for (const p of spec.params) params[p.name] = p.default;
  return { id: newNodeId(spec.type), type: spec.type, params };
}

/**
 * Whether a link is even conceivable, before shapes are considered.
 *
 * Blender lets a float become a colour implicitly; nothing converts implicitly
 * here, so a socket type mismatch is refused at the wire rather than papered over
 * with a reshape we invented.
 */
export function socketsCompatible(
  from: SocketSpec | undefined,
  to: SocketSpec | undefined,
): boolean {
  if (!from || !to) return false;
  return from.type === to.type;
}
