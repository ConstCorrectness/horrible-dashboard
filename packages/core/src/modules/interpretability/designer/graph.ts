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

/** A labelled rectangle behind a set of nodes. Pure cosmetics — see FrameNode.tsx. */
export interface FrameBox {
  id: string;
  label: string;
  color: string;
}

export interface Layout {
  nodes: Record<string, NodeLayout>;
  frames: FrameBox[];
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

export interface ParseResponse {
  graph: DesignGraph | null;
  /** Classes preserved verbatim as `custom.module` nodes because we could not map them. */
  opaque: string[];
  warnings: string[];
  error: string | null;
}

/**
 * Python → a design graph: the other half of the round trip.
 *
 * Deliberately **not** called on every keystroke. A parse mid-word would replace the
 * graph with whatever half-typed source currently means, so this runs only on an
 * explicit sync — blur, or the save key.
 */
export function parseSource(source: string): Promise<ParseResponse> {
  return apiPost<ParseResponse>(`${BASE}/parse`, { source });
}

export interface ProbeResult {
  status: 'ran' | 'failed' | 'unavailable';
  message: string;
  /** The exception, verbatim. Never summarised — the traceback is the answer. */
  traceback: string;
  outputShape: number[];
  totalParams: number | null;
  estimatedParams: number | null;
  /** null when there is nothing to compare; false means our estimate is wrong. */
  agrees: boolean | null;
  /** Node id → measured parameters, summed across every copy a ×N stack made. */
  nodeParams: Record<string, number>;
  /** Node id → whether the measurement matched the estimate for that node. A
   * `false` says *which* node's arithmetic is wrong, which a total never can. */
  nodeAgrees: Record<string, boolean>;
  /** False when the model had more parameter-holding modules than the probe
   * reports, so the per-node picture is partial and must not be read as whole. */
  nodeParamsComplete: boolean;
  project: string;
  torchVersion: string;
  durationMs: number;
}

/**
 * A design derived from the model the Inspect tab is showing.
 *
 * The qualifications are not decoration. `ModelArchitecture` is deliberately full of
 * holes — a dimension the metadata did not state is `None` rather than guessed — so
 * an import either refuses (`missing` non-empty, no graph) or fills the gaps and says
 * exactly which ones it filled. `statedParams` beside `estimatedParams` is the
 * import auditing itself: if the two disagree, something here is wrong.
 */
export interface ImportResult {
  graph: DesignGraph | null;
  model: string;
  source: string;
  assumed: string[];
  missing: string[];
  notes: string[];
  statedParams: number | null;
  estimatedParams: number | null;
  error: string | null;
}

/** Fork the inspected model into an editable design. Saves nothing by itself. */
export function importInspectedModel(): Promise<ImportResult> {
  return apiPost<ImportResult>(`${BASE}/from-model`, {});
}

export interface TrainingProject {
  id: string;
  name: string;
  venv_ready?: boolean;
}

/**
 * Tier 2: build the module in a training project's venv and run a real forward pass.
 *
 * Slow and deliberately manual. Everything else in the pane is our own arithmetic
 * labelled `estimated`; this is the only call that can replace an estimate with a
 * measurement, and it needs a venv with torch, which the backend deliberately lacks.
 */
export function probeGraph(graph: DesignGraph, project: string): Promise<ProbeResult> {
  return apiPost<ProbeResult>(`${BASE}/probe`, { graph, project });
}

/** What handing a design to a training project did, or why it did nothing. */
export interface HandoffResult {
  ok: boolean;
  project: string;
  modulePath: string;
  /** Zero with `ok` true means the module was written but the project has no
   * notebook to wire it into — a real outcome, not a failure. */
  cells: number;
  replaced: boolean;
  className: string;
  message: string;
}

/**
 * Write the design into a training project as `model.py` plus a notebook block.
 *
 * No new execution path — the project's own kernel, venv and Kaggle/Colab push all
 * apply unchanged. The block carries its own marker, so regenerating a *recipe*
 * cannot delete the model and vice versa.
 */
export function handoffToProject(graph: DesignGraph, project: string): Promise<HandoffResult> {
  return apiPost<HandoffResult>(`${BASE}/handoff`, { graph, project });
}

/** A design traced out of a running `nn.Module`, and how much of it we understood. */
export interface TraceResult {
  graph: DesignGraph | null;
  status: 'traced' | 'failed' | 'unavailable';
  message: string;
  traceback: string;
  /** Operations that became a real node type. */
  mapped: number;
  /** Classes that became placeholders, named — an opaque import nobody is told
   * about is indistinguishable from a wrong one. */
  placeholders: string[];
  torchVersion: string;
}

/**
 * Trace `package.module.ClassName` in a training project's venv.
 *
 * The other importer reads someone's *description* of a model; this one reads the
 * model. Slow and manual for the same reason the probe is: it runs a real torch.
 */
export function traceModule(project: string, target: string): Promise<TraceResult> {
  return apiPost<TraceResult>(`${BASE}/from-traced`, { project, target });
}

/** Projects that could host a probe. Read from the training module's own store. */
export function listProjects(): Promise<{ projects: TrainingProject[] }> {
  return apiGet<{ projects: TrainingProject[] }>('/training/projects');
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

/** Unique among the design's groups, which is what a `group` node's id param names. */
export function newGroupId(): string {
  seq += 1;
  return `grp_${Date.now().toString(36)}${seq}`;
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
