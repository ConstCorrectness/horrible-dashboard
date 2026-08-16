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

export interface TraceOptions {
  modelPath: string;
  prompt: string;
  maxTokens?: number;
  layers?: number[];
  attention?: boolean;
  fidelity?: string;
  tokenCap?: number;
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
