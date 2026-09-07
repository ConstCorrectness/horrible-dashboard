/**
 * Typed client for the datasets backend (`/api/datasets/*`).
 *
 * Shared by the browser pane, the builder canvas and the picker strip that the
 * recipe form mounts as a region — so "which dataset, in which shape" is one
 * answer everywhere rather than three.
 */
import { apiDelete, apiGet, apiPost, apiPut } from '../../api';

export interface DatasetSource {
  id: string;
  label: string;
  /** True when refs are paths on this machine rather than remote ids. Decides
   * which `load_dataset` shape a recipe emits, which does not fail cleanly. */
  local: boolean;
}

export interface DatasetRef {
  source: string;
  id: string;
  title: string;
  url: string;
  rows: number | null;
  description: string;
  meta: Record<string, unknown>;
}

export interface Split {
  config: string;
  split: string;
}

/** A format verdict: what shape the rows are in, and the evidence for it. */
export interface Detection {
  format: string;
  confidence: number;
  reason: string;
  columns: Record<string, string>;
  /** Below 0.5 this renders as a guess, not a statement. */
  certain: boolean;
}

export interface Peek {
  source: string;
  id: string;
  config: string;
  split: string;
  columns: string[];
  rows: Record<string, unknown>[];
  detection: Detection;
}

export interface Dataset {
  id: string;
  name: string;
  source: string;
  ref: string;
  config: string;
  split: string;
  format: string;
  column_map: Record<string, string>;
  rows: number | null;
  path: string;
  notes: string;
  fingerprint: string;
  created_at: number;
  updated_at: number;
}

export interface Adaptation {
  ok: boolean;
  columns: Record<string, string>;
  problem: string;
  needsFormatting: boolean;
}

export interface AdaptResult {
  detection: Detection;
  adaptation: Adaptation;
  textField: string;
  formattingSource: string;
  columns: string[];
}

export interface TokenStats {
  sampled: number;
  tokenizer: string;
  /** False means these are character estimates, and the pane says so rather than
   * presenting an estimate as a token count. */
  exact: boolean;
  note: string;
  min: number;
  max: number;
  mean: number;
  p50: number;
  p95: number;
  /** Fraction of sampled examples that would be silently truncated. */
  over_limit: number;
  histogram: { from: number; to: number; count: number }[];
}

export type StepOp =
  | 'load'
  | 'filter'
  | 'template'
  | 'dedupe'
  | 'sample'
  | 'synthesize'
  | 'split'
  | 'write';

export interface BuildStep {
  id: string;
  op: StepOp;
  params: Record<string, unknown>;
  enabled: boolean;
}

export interface Pipeline {
  id: string;
  name: string;
  steps: BuildStep[];
  updated_at: number;
}

export interface StepReport {
  index: number;
  op: string;
  rowsIn?: number;
  rows: number;
  ms?: number;
  skipped?: boolean;
  /** Set when a step removed every row — invisible in the output, obvious here. */
  warning?: string;
}

export interface Preview {
  rows: Record<string, unknown>[];
  columns: string[];
  steps: StepReport[];
  problems: string[];
}

export interface BuildRecord {
  buildId: string;
  name: string;
  state: 'queued' | 'running' | 'finished' | 'failed';
  rows: number;
  steps: StepReport[];
  path?: string;
  datasetId?: string;
  error?: string;
  startedAt: number;
  finishedAt?: number;
}

const base = '/datasets';

export const listSources = () => apiGet<DatasetSource[]>(`${base}/sources`);

export const searchDatasets = (q: string, source = 'hub', limit = 20) =>
  apiGet<DatasetRef[]>(
    `${base}/search?q=${encodeURIComponent(q)}&source=${encodeURIComponent(source)}&limit=${limit}`,
  );

export const listSplits = (ref: string, source = 'hub') =>
  apiGet<Split[]>(
    `${base}/splits?ref=${encodeURIComponent(ref)}&source=${encodeURIComponent(source)}`,
  );

export const peek = (body: {
  source?: string;
  ref: string;
  config?: string;
  split?: string;
  limit?: number;
}) => apiPost<Peek>(`${base}/peek`, body);

export const adapt = (body: {
  dataset_id?: string;
  source?: string;
  ref?: string;
  config?: string;
  split?: string;
  task: string;
}) => apiPost<AdaptResult>(`${base}/adapt`, body);

export const tokenStats = (body: {
  dataset_id?: string;
  source?: string;
  ref?: string;
  config?: string;
  split?: string;
  limit?: number;
  model?: string;
  max_length?: number;
}) => apiPost<TokenStats>(`${base}/token-stats`, body);

export const listDatasets = (source = '') =>
  apiGet<Dataset[]>(`${base}${source ? `?source=${encodeURIComponent(source)}` : ''}`);

export const registerDataset = (body: {
  name?: string;
  source?: string;
  ref: string;
  config?: string;
  split?: string;
  format?: string;
  column_map?: Record<string, string>;
  notes?: string;
}) => apiPost<Dataset>(base, body);

export const getDataset = (id: string) => apiGet<Dataset>(`${base}/${id}`);

export const updateDataset = (
  id: string,
  body: Partial<Pick<Dataset, 'name' | 'split' | 'config' | 'format' | 'notes'>> & {
    column_map?: Record<string, string>;
  },
) => apiPut<Dataset>(`${base}/${id}`, body);

export const deleteDataset = (id: string) => apiDelete<{ ok: boolean }>(`${base}/${id}`);

export const validatePipeline = (pipeline: Pipeline) =>
  apiPost<{ problems: string[] }>(`${base}/builder/validate`, { pipeline, limit: 10 });

export const previewPipeline = (pipeline: Pipeline, limit = 10) =>
  apiPost<Preview>(`${base}/builder/preview`, { pipeline, limit });

export const buildPipeline = (pipeline: Pipeline, name: string, save = true) =>
  apiPost<{ buildId: string }>(`${base}/builder/build`, { pipeline, name, save });

export const builderStatus = (buildId = '') =>
  apiGet<BuildRecord[]>(
    `${base}/builder/status${buildId ? `?build_id=${encodeURIComponent(buildId)}` : ''}`,
  );

/** Human label for a detected shape. The raw ids are backend vocabulary. */
export const FORMAT_LABELS: Record<string, string> = {
  chatml: 'ChatML',
  sharegpt: 'ShareGPT',
  alpaca: 'Alpaca',
  preference: 'Preference pairs',
  raw_text: 'Raw text',
  unknown: 'Unrecognised',
};
