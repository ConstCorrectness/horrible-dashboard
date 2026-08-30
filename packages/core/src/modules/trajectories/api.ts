/**
 * HTTP client for the trajectories backend.
 *
 * Types mirror `backend/modules/trajectories/models.py`. The backend is the source
 * of truth for the shapes; anything added there has to be added here *and* to the
 * Pydantic response model, or it never reaches the browser.
 */

export type TrajectorySource = 'local' | 'evals' | 'games' | 'peer' | 'external' | 'imported';

export type RunStatus = 'running' | 'complete' | 'failed' | 'abandoned';
export type Outcome = 'success' | 'failure' | 'partial' | 'unknown';
export type StepKind = 'message' | 'action' | 'thought' | 'observation' | 'reward' | 'error';

export interface Dataset {
  id: string;
  name: string;
  description: string;
  source_kind: TrajectorySource;
  capture: boolean;
  tags: string[];
  schema_version: number;
  created_at: number;
  updated_at: number;
  run_count: number;
}

export interface TrajectoryStep {
  seq: number;
  kind: StepKind;
  round: number;
  role: string | null;
  name: string | null;
  args: unknown;
  result: unknown;
  ok: boolean | null;
  content: string | null;
  tokens: number | null;
  duration_ms: number | null;
  gated: boolean;
  error: string | null;
  ts: number;
}

export interface TrajectoryLabel {
  id: string;
  run_id: string;
  step_seq: number | null;
  key: string;
  value: string;
  score: number | null;
  source: string;
  rationale: string;
  created_at: number;
}

export interface Harness {
  fingerprint: string;
  agent_id: string;
  model: string;
  provider: string;
  system_prompt: string;
  tool_names: string[];
  params: Record<string, unknown>;
  label: string;
  first_seen: number;
  last_seen: number;
  run_count: number;
}

export interface TrajectoryRun {
  id: string;
  dataset_id: string;
  source: TrajectorySource;
  external_id: string | null;
  turn_id: string | null;
  parent_run_id: string | null;
  harness: string | null;
  agent_id: string;
  agent_name: string;
  model: string;
  provider: string;
  goal: string;
  status: RunStatus;
  outcome: Outcome | null;
  reward: number | null;
  steps: number;
  rounds: number;
  tokens_in: number | null;
  tokens_out: number | null;
  started_at: number;
  finished_at: number | null;
  duration_ms: number | null;
  /** What the run cost, when the provider reported it. Null means unknown. */
  cost_usd: number | null;
  /** Which node/person produced it. Empty (not null) for a local run — the
   * backend declares these as `str = ""`. */
  node_id: string;
  person_id: string;
  error: string;
  meta: Record<string, unknown>;
}

export interface TrajectoryDetail extends TrajectoryRun {
  step_list: TrajectoryStep[];
  labels: TrajectoryLabel[];
  harness_detail: Harness | null;
}

export interface ToolStat {
  name: string;
  calls: number;
  failures: number;
  gated: number;
  failureRate: number;
  avgMs: number | null;
}

export interface Stats {
  runs: number;
  avgSteps: number;
  avgMs: number | null;
  outcomes: Record<string, number>;
  tools: ToolStat[];
}

export interface CompareSide {
  fingerprint: string;
  label: string;
  model: string;
  runs: number;
  graded: number;
  wins: number;
  /** Null, not zero, when nothing is graded — see the backend's analyze.py. */
  successRate: number | null;
  avgSteps: number;
  avgMs: number | null;
  tools: ToolStat[];
}

export interface CompareReport {
  a: CompareSide;
  b: CompareSide;
  pairedGoals: number;
  comparable: boolean;
  note: string;
  pairedSuccess: { a: number; b: number; of: number };
  regressions: string[];
  fixes: string[];
  toolDelta: { name: string; a: number; b: number; delta: number }[];
}

const BASE = '/api/trajectories';

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const listDatasets = () => req<{ datasets: Dataset[] }>('/datasets').then((r) => r.datasets);

export const createDataset = (body: { id: string; name: string; description?: string }) =>
  req<Dataset>('/datasets', { method: 'POST', body: JSON.stringify(body) });

export const updateDataset = (id: string, body: Record<string, unknown>) =>
  req<Dataset>(`/datasets/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });

export const deleteDataset = (id: string) =>
  req<{ ok: boolean }>(`/datasets/${encodeURIComponent(id)}`, { method: 'DELETE' });

export function listRuns(params: {
  dataset?: string;
  outcome?: string;
  harness?: string;
  source?: string;
  q?: string;
  limit?: number;
  offset?: number;
}) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') qs.set(key, String(value));
  }
  return req<{ runs: TrajectoryRun[]; total: number }>(`/runs?${qs.toString()}`);
}

export const getRun = (id: string) => req<TrajectoryDetail>(`/runs/${encodeURIComponent(id)}`);

export const deleteRun = (id: string) =>
  req<{ ok: boolean }>(`/runs/${encodeURIComponent(id)}`, { method: 'DELETE' });

export const addLabel = (
  id: string,
  body: { key: string; value?: string; source?: string; rationale?: string },
) =>
  req<TrajectoryLabel>(`/runs/${encodeURIComponent(id)}/labels`, {
    method: 'POST',
    body: JSON.stringify({ source: 'human', ...body }),
  });

export const getStats = (dataset?: string) =>
  req<Stats>(`/stats${dataset ? `?dataset=${encodeURIComponent(dataset)}` : ''}`);

export const listHarnesses = () =>
  req<{ harnesses: Harness[] }>('/harnesses').then((r) => r.harnesses);

export const compareHarnesses = (a: string, b: string) =>
  req<CompareReport>(`/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);

export const getHarness = (fingerprint: string) =>
  req<Harness>(`/harnesses/${encodeURIComponent(fingerprint)}`);

export const listTools = (params: { dataset?: string; harness?: string; limit?: number } = {}) => {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') qs.set(key, String(value));
  }
  return req<{ tools: ToolStat[] }>(`/tools?${qs.toString()}`).then((r) => r.tools);
};

/**
 * How a search answer was actually produced.
 *
 * The backend degrades rather than failing — no embedder answered, or the query was
 * empty — and it says which it did. That has to reach the screen: a silent fall back
 * to `recent` looks exactly like a semantic search that returned nothing useful.
 */
export type SearchMethod = 'semantic' | 'substring' | 'recent';

export const searchRuns = (body: {
  query: string;
  dataset?: string | null;
  /** Successes only by default on the backend; pass null to search everything. */
  outcome?: string | null;
  harness?: string | null;
  limit?: number;
}) =>
  req<{ runs: TrajectoryRun[]; method: SearchMethod }>('/search', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const reindex = (dataset?: string, full = false) => {
  const qs = new URLSearchParams();
  if (dataset) qs.set('dataset', dataset);
  if (full) qs.set('full', 'true');
  return req<Record<string, number>>(`/reindex?${qs.toString()}`, { method: 'POST' });
};

export interface ExportReport {
  path: string;
  examples: number;
  candidates: number;
  skipped: string[];
  skippedCount: number;
  note: string;
}

export const exportSft = (body: {
  name?: string;
  dataset?: string | null;
  harness?: string | null;
  label_source?: string | null;
  limit?: number;
}) => req<ExportReport>('/export', { method: 'POST', body: JSON.stringify(body) });

export interface IngestReport {
  run_ids: string[];
  created: number;
  merged: number;
}

/** The formats `adapters/importers.py` understands. Kept in step by hand. */
export const IMPORT_FORMATS = ['claude-code', 'openai', 'messages'] as const;
export type ImportFormat = (typeof IMPORT_FORMATS)[number];

export const importRuns = (body: {
  dataset_id: string;
  format: ImportFormat;
  content: string;
}) => req<IngestReport>('/import', { method: 'POST', body: JSON.stringify(body) });

export const importReplay = (replayId: string, datasetId = 'games') =>
  req<IngestReport>(
    `/import/replay/${encodeURIComponent(replayId)}?dataset_id=${encodeURIComponent(datasetId)}`,
    { method: 'POST' },
  );
