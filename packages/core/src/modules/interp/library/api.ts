/**
 * The interpretability library and worker endpoints (`/api/interp`). Shapes mirror
 * `backend/modules/interp/routes.py`.
 */
import { apiDelete, apiGet, apiPost, streamNdjson } from '../../../api';

export interface CatalogEntry {
  id: string;
  kind: 'sae' | 'transcoder' | 'clt' | 'jlens' | 'axis';
  title: string;
  repo: string;
  repoType: 'model' | 'dataset';
  /** `folder`: one choice per folder under `root`; `repo`: the whole repo is one. */
  pick: 'folder' | 'repo';
  root: string;
  include: string[];
  /** The HF model it was trained on; `{choice}` when it differs per folder. */
  forModel: string;
  note: string;
}

export interface Choice {
  /** Folder path, or `""` for a whole repo. */
  id: string;
  files: { path: string; size: number }[];
  bytes: number;
  forModel: string;
  installed?: boolean;
}

export type CompatState = 'compatible' | 'incompatible' | 'unknown';

export interface Compat {
  state: CompatState;
  reasons: string[];
  target: string;
  source: 'gguf' | 'worker';
}

export interface ArtifactFacts {
  dIn?: number;
  width?: number;
  layer?: number;
  /** Where each fact came from: a config file's name, or `folder name`. */
  source: Record<string, string>;
}

export interface Artifact {
  id: string;
  entryId: string;
  kind: CatalogEntry['kind'];
  repo: string;
  choice: string;
  modelRepo: string | null;
  path: string;
  bytes: number;
  facts: ArtifactFacts;
  createdAt: number;
  compat: Compat[];
}

export interface Target {
  source: 'gguf' | 'worker';
  label: string;
  repo: string | null;
  dModel: number | null;
  layers: number | null;
}

export interface ArtifactsReply {
  artifacts: Artifact[];
  targets: Target[];
  usedBytes: number;
  budgetBytes: number;
}

export type WorkerState = 'no_env' | 'stopped' | 'starting' | 'ready' | 'crashed';

export interface EnvStatus {
  state: 'idle' | 'installing' | 'ready' | 'failed';
  group: string;
  line: string;
  error: string;
  path: string;
  exists: boolean;
  groups: string[];
  available: string[];
}

export interface LoadedModel {
  repo: string;
  dtype: string;
  device: string;
  layers: number;
  dModel: number;
  loadSeconds: number;
}

export interface WorkerInfo {
  env: EnvStatus;
  state: WorkerState;
  pid: number | null;
  ops: string[];
  exitCode: number | null;
  log: string[];
  ping?: {
    python: string;
    torch: string;
    transformers: string;
    cuda: boolean;
    device?: string;
    vramBytes?: number;
  } | null;
  status?: { loaded: LoadedModel | null; vramAllocated?: number; vramReserved?: number } | null;
}

/** Every worker op's reply: it ran, it failed (verbatim traceback), or it declined. */
export type WorkerReply =
  | { state: 'ran'; result: Record<string, unknown> }
  | { state: 'failed'; traceback: string }
  | { state: 'not_checked'; reason: string };

export function getCatalog(): Promise<{ entries: CatalogEntry[] }> {
  return apiGet('/interp/library/catalog');
}

export function getChoices(entryId: string): Promise<{ choices: Choice[]; error?: string }> {
  return apiGet(`/interp/library/catalog/${encodeURIComponent(entryId)}/choices`);
}

export function getArtifacts(): Promise<ArtifactsReply> {
  return apiGet('/interp/library/artifacts');
}

export function deleteArtifact(id: string): Promise<{ ok: boolean }> {
  // `:path` on the backend: the id's folder slashes stay slashes.
  return apiDelete(`/interp/library/artifacts/${id.split('/').map(encodeURIComponent).join('/')}`);
}

export interface DownloadProgress {
  status?: 'downloading' | 'done';
  total?: number;
  completed?: number;
  file?: string;
  artifact?: Artifact;
  error?: string;
}

/** Download one choice. `confirmBytes` is the size the user agreed to; the backend
 *  refuses (409) if the Hub now reports another. */
export function downloadArtifact(
  entryId: string,
  choice: string,
  confirmBytes: number,
  onProgress: (p: DownloadProgress) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson(
    '/interp/library/download',
    { entryId, choice, confirmBytes },
    (obj) => onProgress(obj as DownloadProgress),
    signal,
  );
}

export function getWorker(): Promise<WorkerInfo> {
  return apiGet('/interp/worker');
}

export function installEnv(group: string): Promise<{ started: boolean; env: EnvStatus }> {
  return apiPost('/interp/worker/env/install', { group });
}

export function startWorker(): Promise<{ ok: boolean; error?: string; state: WorkerState }> {
  return apiPost('/interp/worker/start', {});
}

export function stopWorker(): Promise<{ state: WorkerState }> {
  return apiPost('/interp/worker/stop', {});
}

export interface FeatureCard {
  model?: string;
  source?: string;
  index?: string;
  hook?: string;
  explanations?: { text: string; by: string | null }[];
  promotes?: string[];
  suppresses?: string[];
  maxActivation?: number | null;
  density?: number | null;
  /** Top activating contexts, each windowed around its peak token. */
  examples?: { tokens: string[]; values: number[]; peak: number; maxValue: number | null }[];
  url?: string;
  cached?: boolean;
  error?: string;
}

/** One Neuronpedia feature by public id. A GET of public data, cached a week. */
export function getFeature(model: string, source: string, index: string): Promise<FeatureCard> {
  const q = new URLSearchParams({ model, source, index });
  return apiGet(`/interp/neuronpedia/feature?${q.toString()}`);
}

export function workerOp(op: string, args: Record<string, unknown> = {}): Promise<WorkerReply> {
  return apiPost('/interp/worker/op', { op, args });
}
