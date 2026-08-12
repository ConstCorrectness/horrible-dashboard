import { apiGet, apiPost, streamNdjson } from '../../api';

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
  gpuLayers?: number;
  threads?: number;
}

export function startServer(options: StartOptions): Promise<LlamaStatus> {
  return apiPost<LlamaStatus>('/llamacpp/start', options);
}

export function stopServer(): Promise<LlamaStatus> {
  return apiPost<LlamaStatus>('/llamacpp/stop', {});
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return '—';
  // Zero is a real answer here — an empty managed directory reads "0 B of 80 GB
  // budget", not "— of 80 GB budget", which looks like a failed lookup.
  if (bytes <= 0) return '0 B';
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
}

/** Parameter counts read from the tensor inventory, so "7.6B" not "7B". */
export function formatParams(count: number | null): string {
  if (!count) return '—';
  if (count >= 1e9) return `${(count / 1e9).toFixed(1)}B`;
  if (count >= 1e6) return `${(count / 1e6).toFixed(0)}M`;
  return String(count);
}
