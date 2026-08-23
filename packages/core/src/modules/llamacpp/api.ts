import { apiDelete, apiGet, apiPost, streamNdjson } from '../../api';

/** One unpacked `llama-server` build under the data dir. */
export interface Install {
  tag: string;
  variant: string;
  path: string;
  binary: string;
  sizeBytes: number;
  sha256: string;
  /**
   * True only when GitHub published a digest for the asset and it matched. False
   * means "we hashed what we got and recorded it" — worth showing, and not the
   * same claim.
   */
  verified: boolean;
  asset: string;
}

export interface LlamaStatus {
  installed: boolean;
  install: Install | null;
  installs: Install[];
  running: boolean;
  /** The server is up AND has finished loading the model. Loading a large GGUF
   * takes tens of seconds, during which `running` is true and requests fail. */
  ready: boolean;
  modelPath: string | null;
  model: string;
  endpoint: string;
  pid: number | null;
  error: string;
  uptimeSeconds: number;
  logs: string[];
  isAgentProvider: boolean;
}

export interface ModelEntry {
  path: string;
  /** managed | ollama | lmstudio | extra — only `managed` is deletable here. */
  origin: string;
  name: string;
  sizeBytes: number;
  architecture: string;
  parameters: number | null;
  contextLength: number | null;
  quantization: string;
  error: string;
  deletable: boolean;
}

export interface ModelsResponse {
  models: ModelEntry[];
  usedBytes: number;
  budgetBytes: number;
  root: string;
  extraDirs: string[];
  suggested: { repo: string; label: string; note: string }[];
}

/**
 * Where one GGUF's bytes sit, block by block — the input to the offload preview.
 *
 * `layerBytes` are the file's real tensor sizes, mixed quantization and all.
 * `overheadBytes` is everything outside the stack (embeddings, final norm, output
 * head) and is separate because `--n-gpu-layers` only reaches it once the count
 * exceeds the block count. `kvBytesPerToken` is per *token* so the caller can
 * multiply by a context size the user is still free to change.
 */
export interface LayerPlan {
  path: string;
  layerCount: number;
  layerBytes: number[];
  overheadBytes: number;
  totalBytes: number;
  kvBytesPerToken: number | null;
  contextLength: number | null;
  /** False when a tensor's quantization was unrecognized: totals are a floor. */
  complete: boolean;
  error: string;
}

export function getLayerPlan(path: string): Promise<LayerPlan> {
  return apiGet<LayerPlan>(`/llamacpp/models/layers?path=${encodeURIComponent(path)}`);
}

export interface RepoFile {
  path: string;
  sizeBytes: number;
  isProjector: boolean;
}

/** A progress line from an install or a download. Terminal on `error` or `done`. */
export interface Progress {
  status?: string;
  completed?: number;
  total?: number;
  error?: string;
  [key: string]: unknown;
}

export function getLlamaStatus(): Promise<LlamaStatus> {
  return apiGet<LlamaStatus>('/llamacpp/status');
}

export function getLlamaModels(): Promise<ModelsResponse> {
  return apiGet<ModelsResponse>('/llamacpp/models');
}

/** Which variants the current llama.cpp release actually publishes for this OS/arch. */
export interface VariantAvailability {
  tag: string;
  os: string;
  arch: string;
  variants: Record<string, boolean>;
  error: string;
}

export function getInstallVariants(tag = 'latest'): Promise<VariantAvailability> {
  return apiGet<VariantAvailability>(`/llamacpp/install/variants?tag=${encodeURIComponent(tag)}`);
}

export function getRepoFiles(
  repo: string,
): Promise<{ repo: string; files: RepoFile[]; error: string }> {
  return apiGet(`/llamacpp/repo?repo=${encodeURIComponent(repo)}`);
}

export function installServer(
  tag: string,
  variant: string,
  onProgress: (p: Progress) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson(
    '/llamacpp/install',
    { tag, variant },
    (obj) => onProgress(obj as Progress),
    signal,
  );
}

export function downloadModel(
  repo: string,
  file: string,
  onProgress: (p: Progress) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson(
    '/llamacpp/models/download',
    { repo, file },
    (obj) => onProgress(obj as Progress),
    signal,
  );
}

export function deleteModel(path: string): Promise<{ deleted: boolean }> {
  return apiPost('/llamacpp/models/delete', { path });
}

export interface StartOptions {
  modelPath: string;
  alias?: string;
  contextSize?: number;
  /**
   * Layers offloaded to the GPU. **`null` means "ask the hardware probe"** and an
   * explicit `0` means pure CPU — they are different requests, which is why this is
   * nullable rather than defaulting to 0. Sending 0 because a form field had to hold
   * *something* is how a machine with a 4090 ends up running on its CPU.
   */
  gpuLayers?: number | null;
  threads?: number | null;
}

export function startServer(options: StartOptions): Promise<LlamaStatus> {
  return apiPost<LlamaStatus>('/llamacpp/start', options);
}

export function stopServer(): Promise<LlamaStatus> {
  return apiPost<LlamaStatus>('/llamacpp/stop', {});
}

/**
 * One captured node.
 *
 * `fidelity` is the honesty knob and must survive to the screen: `full` is the
 * tensor, `fp16` is a downcast of it, and `summary` is statistics *instead of*
 * the tensor — a summary record has no values and must never be drawn as if it
 * had.
 */
export interface TraceRecord {
  index: number;
  name: string;
  op: string;
  dtype: string;
  ne: number[];
  nb: number[];
  /** The decoder block, or null for nodes outside one (embeddings, the head). */
  layer: number | null;
  passIndex: number;
  fidelity: 'full' | 'fp16' | 'summary';
  offset: number;
  length: number;
  summary: Record<string, number>;
}

export interface TraceSummary {
  traceId: string;
  createdAt: number;
  modelName: string;
  modelPath: string;
  modelSha: string;
  /** What `modelSha` actually hashed — never a whole-file digest. */
  modelShaScope: string;
  llamaBuild: string;
  flashAttn: boolean;
  fidelity: string;
  attention: boolean;
  prompt: string;
  promptTokens: number;
  maxTokens: number;
  recordCount: number;
  blobBytes: number;
  diskBytes: number;
  chatTemplate: boolean;
  note: string;
  /** "ids" when the tokens were supplied rather than tokenized from `prompt` —
   * the difference between a trace reproducible from the text above and one
   * that is not. */
  tokenSource?: 'text' | 'ids';
  /** Set on a fork: the trace it came from, and what was changed. */
  derivedFrom?: string;
  edits?: TokenEdit[];
  capture?: string[];
}

export interface TraceListResponse {
  traces: TraceSummary[];
  usedBytes: number;
  budgetBytes: number;
  root: string;
  /** False when `llama-cpp-python` is missing; `reason` carries the install line. */
  available: boolean;
  reason: string;
}

export interface TraceToken {
  index: number;
  id: number;
  text: string;
  generated: boolean;
}

export interface TraceDetail {
  trace: TraceSummary;
  records: TraceRecord[];
  tokens: TraceToken[];
}

export interface RecordValues {
  record: TraceRecord;
  values: number[];
  truncated: boolean;
  summary: Record<string, number>;
}

export interface TraceEstimate {
  bytes: number;
  seconds: number;
  note: string;
  layers: number;
  embeddingLength: number;
  heads: number;
  promptTokens: number;
  budgetBytes: number;
  error: string;
}

export interface TokenEdit {
  position: number;
  /** Stamped by the backend from the parent, never trusted from here. */
  fromId?: number;
  toId: number;
}

export interface TraceOptions {
  modelPath: string;
  prompt: string;
  maxTokens?: number;
  layers?: number[];
  attention?: boolean;
  fidelity?: string;
  tokenCap?: number;
  /**
   * Exact tokens, bypassing tokenization. Re-tokenizing an edited *string* can
   * merge or split its neighbours, so a swap expressed as text would change the
   * run in more places than the one you meant.
   */
  tokenIds?: number[];
  /** Graph-node patterns; `[]` is the architecture's own default set. */
  capture?: string[];
}

export function listTraces(): Promise<TraceListResponse> {
  return apiGet<TraceListResponse>('/llamacpp/traces');
}

export function getTrace(traceId: string): Promise<TraceDetail> {
  return apiGet<TraceDetail>(`/llamacpp/traces/${encodeURIComponent(traceId)}`);
}

export function getRecordValues(traceId: string, index: number): Promise<RecordValues> {
  return apiGet<RecordValues>(`/llamacpp/traces/${encodeURIComponent(traceId)}/record/${index}`);
}

/** One pass's value of a watched node's statistic. `value` is null when that pass
 * has the node but nothing measured — drawn as a gap, never interpolated. */
export interface SeriesPoint {
  passIndex: number;
  value: number | null;
  fidelity: string;
}

export interface TraceSeries {
  name: string;
  stat: string;
  points: SeriesPoint[];
  error: string;
}

/** A watched node's statistic across every forward pass. Addressed by **name**,
 * because that is what a pin is — the same node in pass 3 is a different record. */
export function getTraceSeries(traceId: string, name: string, stat = 'rms'): Promise<TraceSeries> {
  return apiGet<TraceSeries>(
    `/llamacpp/traces/${encodeURIComponent(traceId)}/series` +
      `?name=${encodeURIComponent(name)}&stat=${encodeURIComponent(stat)}`,
  );
}

export function estimateTrace(options: TraceOptions): Promise<TraceEstimate> {
  return apiPost<TraceEstimate>('/llamacpp/traces/estimate', options);
}

export function runTrace(
  options: TraceOptions,
  onProgress: (p: Progress) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson('/llamacpp/traces', options, (obj) => onProgress(obj as Progress), signal);
}

// ── the lens ────────────────────────────────────────────────────────────────

/** A transport applied before unembedding. `identity` is the classic logit lens
 * and is always available; a `jacobian` lens is a fitted artifact on disk. */
export interface LensSpec {
  id: string;
  kind: 'identity' | 'jacobian';
  label: string;
  provenance: string;
  layers: number[];
  dModel: number;
}

/** One (layer, position) readout. `relProbs` is a softmax over the shown
 * candidates only — not the model's distribution, and never rendered as one. */
export interface LensCell {
  ids: number[];
  texts: string[];
  logits: number[];
  relProbs: number[];
}

export interface LensGrid {
  layers: number[];
  positions: number[];
  cells: LensCell[][];
  lens: LensSpec;
  unembedding: {
    tensor: string;
    tied: boolean;
    quant: string;
    nEmbd: number;
    nVocab: number;
    architecture: string;
    tokenizerModel: string;
    logitSoftcap: number | null;
  };
  tokens: TraceToken[];
  /**
   * Three-valued on purpose. `true`: the identity lens reproduced this trace's
   * own captured logits. `false`: it did not, and every cell is suspect.
   * `unavailable`: there was nothing to check against. Rendering the third as
   * the first is the failure this whole surface exists to refuse.
   */
  verified: 'true' | 'false' | 'unavailable';
  verifyNote: string;
  verifyDetail: Record<string, unknown>;
}

export interface LensTrack {
  tokenId: number;
  text: string;
  layers: number[];
  positions: number[];
  logits: number[][];
  ranks: number[][];
  lens: LensSpec;
}

export interface LensListResponse {
  lenses: LensSpec[];
  available: boolean;
  reason: string;
}

export interface VocabEntry {
  id: number;
  /** The raw GGUF entry ("Ġthe"), so a search typed in the encoding still hits. */
  piece: string;
  text: string;
}

export interface VocabResponse {
  tokens: VocabEntry[];
  total: number;
  tokenizerModel: string;
  truncated: boolean;
}

export interface CaptureSet {
  id: string;
  label: string;
  patterns: string[];
  note: string;
}

export function getLensGrid(
  traceId: string,
  options: { lens?: string; k?: number; layers?: number[]; positions?: number[] } = {},
): Promise<LensGrid> {
  const query = new URLSearchParams();
  if (options.lens) query.set('lens', options.lens);
  if (options.k) query.set('k', String(options.k));
  if (options.layers?.length) query.set('layers', options.layers.join(','));
  if (options.positions?.length) query.set('positions', options.positions.join(','));
  const suffix = query.toString() ? `?${query}` : '';
  return apiGet<LensGrid>(`/llamacpp/traces/${encodeURIComponent(traceId)}/lens${suffix}`);
}

export function getLensTrack(traceId: string, tokenId: number, lens = 'identity'): Promise<LensTrack> {
  return apiGet<LensTrack>(
    `/llamacpp/traces/${encodeURIComponent(traceId)}/lens/track` +
      `?tokenId=${tokenId}&lens=${encodeURIComponent(lens)}`,
  );
}

export function listLenses(traceId: string): Promise<LensListResponse> {
  return apiGet<LensListResponse>(`/llamacpp/traces/${encodeURIComponent(traceId)}/lenses`);
}

export function searchVocab(path: string, q: string, limit = 50): Promise<VocabResponse> {
  return apiGet<VocabResponse>(
    `/llamacpp/models/vocab?path=${encodeURIComponent(path)}` +
      `&q=${encodeURIComponent(q)}&limit=${limit}`,
  );
}

/** The capture sets, served rather than restated here — two lists of ggml node
 * names in two languages is one upstream rename from matching nothing. */
export function getCaptureSets(): Promise<{ sets: CaptureSet[] }> {
  return apiGet<{ sets: CaptureSet[] }>('/llamacpp/traces/capture-sets');
}

/** Re-run a trace with some of its tokens replaced. Everything else is
 * inherited from the parent, so the two are comparable. */
export function forkTrace(
  traceId: string,
  edits: TokenEdit[],
  onProgress: (p: Progress) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson(
    `/llamacpp/traces/${encodeURIComponent(traceId)}/fork`,
    { edits },
    (obj) => onProgress(obj as Progress),
    signal,
  );
}

export function deleteTrace(traceId: string): Promise<{ deleted: boolean }> {
  return apiDelete(`/llamacpp/traces/${encodeURIComponent(traceId)}`);
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return '—';
  // Zero is a real answer here — an empty managed directory reads "0 B of 80 GB
  // budget", not "— of 80 GB budget", which looks like a failed lookup.
  if (bytes <= 0) return '0 B';
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  // Traces are orders of magnitude smaller than weights, and a 16 KB one
  // rendered as "0 MB" reads as an empty trace rather than a small one.
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

/** Parameter counts read from the tensor inventory, so "7.6B" not "7B". */
export function formatParams(count: number | null): string {
  if (!count) return '—';
  if (count >= 1e9) return `${(count / 1e9).toFixed(1)}B`;
  if (count >= 1e6) return `${(count / 1e6).toFixed(0)}M`;
  return String(count);
}
