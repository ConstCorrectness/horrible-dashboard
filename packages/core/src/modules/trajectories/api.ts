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
  /** Friends may list, pull and live-watch its runs. Never true for a `peer` dataset. */
  shared: boolean;
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
  /** Explicit parent step, for natively nested traces (a received OTel trace, a
   * LangGraph subgraph). Null means "nesting derives from `round`". */
  parent_seq?: number | null;
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
  /** `running` is what the Live view asks for on mount — see `ws.ts`. */
  status?: RunStatus;
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

/** One persisted request this run made. Mirrors `telemetry_events`. */
export interface RunIoEvent {
  /** Per-process id. `ev_id` restarts at 1 on every boot, so identity is the pair. */
  boot_id: string;
  ev_id: number;
  turn_id: string;
  round: number | null;
  ts: number;
  source: string;
  method: string;
  target: string;
  status: number | null;
  duration_ms: number | null;
  request_bytes: number | null;
  response_bytes: number | null;
  error: string | null;
  verdict: string | null;
  remote_ip: string | null;
  http_protocol: string | null;
  timing: Record<string, number> | null;
  detail: Record<string, unknown> | null;
}

export interface RunIoResponse {
  events: RunIoEvent[];
  /**
   * False when the run has no `turn_id` to join on — anything imported or pushed in
   * by the SDK. Distinct from an empty list, which means the turn genuinely made no
   * requests; rendering both the same way would claim a fact we do not have.
   */
  joinable: boolean;
}

export const getRunIo = (id: string, round?: number) =>
  req<RunIoResponse>(
    `/runs/${encodeURIComponent(id)}/io${round == null ? '' : `?round=${round}`}`,
  );

/** One MCP call's durable summary. Mirrors `backend/modules/mcp/calls.py`. */
export interface McpCallSummary {
  id: string;
  server_id: string;
  tool: string;
  turn_id: string | null;
  round: number | null;
  started_at: number;
  duration_ms: number | null;
  ok: boolean;
  /** `tool`: the server answered and said the call failed. `transport`: it never
   * answered usefully (not connected, timed out, the session raised). */
  error_kind: 'tool' | 'transport' | null;
  error: string | null;
  request_bytes: number | null;
  response_bytes: number | null;
  content_blocks: number | null;
  rpc_ids: string[];
  /** The connection the call went out on. JSON-RPC ids restart per connection, so
   * `(session, rpc_ids)` identifies the exchange and the ids alone do not. */
  session: string;
}

export interface McpWireMessage {
  at: number;
  direction: 'in' | 'out';
  method: string;
  id: string;
  payload: string;
  truncated: boolean;
  turn_id: string | null;
  round: number | null;
  session: string;
}

/** MCP steps keyed by step `seq`. */
export const getRunMcp = (id: string) =>
  req<{ calls: Record<string, McpCallSummary>; joinable: boolean }>(
    `/runs/${encodeURIComponent(id)}/mcp`,
  );

export const getStepWire = (id: string, seq: number) =>
  req<{ call: McpCallSummary; messages: McpWireMessage[]; available: boolean }>(
    `/runs/${encodeURIComponent(id)}/mcp/${seq}/wire`,
  );

// --- friends ---------------------------------------------------------------

export interface PeerDevice {
  node_id: string;
  label: string;
  /** Null when the friend's build does not report it — not the same as zero. */
  shared_datasets: number | null;
}

export interface PeerPerson {
  person_id: string;
  name: string;
  devices: PeerDevice[];
}

export interface PeerDataset {
  id: string;
  name: string;
  description: string;
  run_count: number;
}

/** A friend's run header. Their node withholds turn ids, identities and meta. */
export type PeerRun = Pick<
  TrajectoryRun,
  | 'id'
  | 'dataset_id'
  | 'agent_id'
  | 'agent_name'
  | 'model'
  | 'provider'
  | 'goal'
  | 'status'
  | 'outcome'
  | 'reward'
  | 'steps'
  | 'rounds'
  | 'tokens_in'
  | 'tokens_out'
  | 'cost_usd'
  | 'started_at'
  | 'finished_at'
  | 'duration_ms'
  | 'error'
  | 'harness'
>;

export interface PeerRunDetail {
  run: PeerRun;
  steps: TrajectoryStep[];
  labels: { key: string; value: string; score: number | null; source: string }[];
}

export interface WatchedSession {
  session_id: string;
  host: string;
  host_name: string;
  title: string;
  grant: string;
}

export const listPeers = () => req<{ people: PeerPerson[] }>('/peers').then((r) => r.people);

export const listPeerDatasets = (node: string) =>
  req<{ datasets: PeerDataset[] }>(`/peers/${encodeURIComponent(node)}/datasets`).then(
    (r) => r.datasets,
  );

export const listPeerRuns = (node: string, dataset: string, status?: RunStatus) => {
  const qs = new URLSearchParams({ dataset });
  if (status) qs.set('status', status);
  return req<{ runs: PeerRun[] }>(`/peers/${encodeURIComponent(node)}/runs?${qs}`).then(
    (r) => r.runs,
  );
};

export const getPeerRun = (node: string, runId: string) =>
  req<PeerRunDetail>(`/peers/${encodeURIComponent(node)}/runs/${encodeURIComponent(runId)}`);

export const pullPeerRun = (node: string, runId: string) =>
  req<{ run_id: string }>(
    `/peers/${encodeURIComponent(node)}/runs/${encodeURIComponent(runId)}/pull`,
    { method: 'POST' },
  );

/**
 * A run as published to the agent commons: its shape, never its payloads. Mirrors
 * `CommonsTrajectoryDigest` in `backend/modules/network/models.py`.
 */
export interface CommonsDigest {
  schema_version: number;
  digest_id: string;
  node_id: string;
  public_key: string;
  published_at?: number;
  harness_fingerprint: string;
  model: string;
  provider: string;
  tool_names: string[];
  goal: string | null;
  status: string;
  outcome: string | null;
  reward: number | null;
  rounds: number;
  steps: {
    seq: number;
    kind: string;
    round: number;
    name: string;
    ok: boolean | null;
    duration_ms: number | null;
    gated: boolean;
  }[];
  tokens_in: number | null;
  tokens_out: number | null;
  duration_ms: number | null;
  /** Index-held, not signed: the publisher's commons profile name. */
  publisher_name?: string;
}

/** Exactly what `publishRun` would send, and the code that confirms it. */
export const getCommonsDigest = (id: string, includeGoal: boolean) =>
  req<{ digest: CommonsDigest; confirm: string }>(
    `/runs/${encodeURIComponent(id)}/commons-digest?include_goal=${includeGoal}`,
  );

export const publishRun = (id: string, body: { include_goal: boolean; confirm: string }) =>
  req<{ digest_id: string; duplicate: boolean }>(`/runs/${encodeURIComponent(id)}/publish`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const listCommons = (mine: boolean) =>
  req<{ connected: boolean; me: string; digests: CommonsDigest[] }>(`/commons?mine=${mine}`);

export const unpublishDigest = (digestId: string) =>
  req<{ ok: boolean }>(`/commons/${encodeURIComponent(digestId)}`, { method: 'DELETE' });

export const listWatching = () =>
  req<{ sessions: WatchedSession[] }>('/watching').then((r) => r.sessions);

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
