import { apiDelete, apiGet, apiPost, apiPut } from '../../api';

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
}

/** Mirrors the backend `HfBenchmark`. Present only on `hf_benchmark` cases. */
export interface HfBenchmark {
  dataset: string;
  config: string;
  split: string;
  input_template: string;
  target_column: string;
  /** Pull the gradeable answer out of the reference. The field that decides
   * whether a benchmark measures the model or the dataset's prose. */
  target_regex: string;
  prediction_regex: string;
  metric: string;
  limit: number;
  threshold: number;
  system: string;
}

/** Mirrors the backend `EvalCase`. */
export interface EvalCase {
  id: string;
  type: string;
  prompt: string;
  expose: { mode: string; preload: string[] };
  expect: { grade: string; calls: ToolCall[] };
  /** Required when `type` is `hf_benchmark`; its presence is what routes the case
   * to the project-venv runner instead of the in-node one. */
  benchmark?: HfBenchmark | null;
  fixtures?: Record<string, unknown>;
  context?: Record<string, unknown>;
  tags: string[];
  note: string;
}

export const GRADES = ['exact', 'name_only', 'subset', 'sequence', 'no_call'] as const;
export const EXPOSE_MODES = ['progressive', 'all', 'explicit'] as const;

export const CASE_TYPES = [
  { value: 'tool_call', label: 'Tool call — does the model pick the right tool' },
  { value: 'hf_benchmark', label: 'HF benchmark — score a Hub dataset' },
] as const;

/** `exact_match` and `contains` are scored by the harness itself; anything else is
 * handed to `evaluate`, which the runner installs on demand. */
export const METRICS = ['contains', 'exact_match', 'rouge', 'bleu'] as const;

/** A blank benchmark block. The defaults are deliberately runnable-but-small: a
 * benchmark you cannot run fifty rows of is one you run once and never again. */
export function emptyBenchmark(): HfBenchmark {
  return {
    dataset: '',
    config: '',
    split: 'test[:50]',
    input_template: '{question}',
    target_column: 'answer',
    target_regex: '',
    prediction_regex: '',
    metric: 'contains',
    limit: 50,
    threshold: 0.3,
    system: '',
  };
}

/** A blank case, with the defaults the backend would apply anyway. Spelled out so
 * the editor never PUTs a partial object the parser would reject. */
export function emptyCase(): EvalCase {
  return {
    id: '',
    type: 'tool_call',
    prompt: '',
    expose: { mode: 'progressive', preload: [] },
    expect: { grade: 'subset', calls: [] },
    tags: [],
    note: '',
  };
}

export interface EvalSuite {
  id: string;
  name: string;
  description: string;
  path: string;
  case_count: number;
  tags: string[];
  /** `bundled` ships with the repo and is read-only; `user` is yours. */
  source: 'user' | 'bundled';
  read_only: boolean;
}

export interface CaseResult {
  case_id: string;
  passed: boolean;
  grade: string;
  detail: string;
  expected: ToolCall[];
  actual: ToolCall[];
  answer: string;
  rounds: number;
  tools_offered: number;
  tools_dropped: string[];
  groups_loaded: string[];
  duration_ms: number;
  error: string;
  turn_id: string;
}

export interface EvalRun {
  id: string;
  suite_id: string;
  label: string;
  provider: string;
  endpoint: string;
  model: string;
  status: string;
  total: number;
  passed: number;
  completed: number;
  started_at: string;
  finished_at: string;
  error: string;
  localtrack_run_id: string;
  /** The peer this ran on, empty for local. Recorded rather than inferred: a peer
   *  target reaches its lender through a *local* tunnel port, so the endpoint on
   *  the row reads as `127.0.0.1` either way. */
  node: string;
  /** Content hash of the tool catalog this run saw — enabled skills plus connected
   *  MCP servers. Empty on runs recorded before it existed, which reads as "cannot
   *  tell", never as agreement. */
  harness_hash: string;
  /** The harness itself, so a differing hash can say what differed. */
  harness_json: string;
}

export interface SuggestedTarget {
  provider: string;
  endpoint: string;
  model: string;
  /** For `llamacpp`: the GGUF to load. The server holds one model at a time, so a
   * target names the file rather than trusting an alias to already be served. */
  modelPath?: string;
  /** Whether llama-server currently has this GGUF loaded. Purely informational —
   * the sweep loads whatever it needs and restores what was there. */
  loaded?: boolean;
  /** From the GGUF header. Shown, not filtered on: it is what tells an embedder
   * apart from a chat model when both live in the same directory. */
  architecture?: string;
  label: string;
  source: string;
}

export const listSuites = () =>
  apiGet<{ suites: EvalSuite[] }>('/evals/suites').then((r) => r.suites);

export const createSuite = (name: string) => apiPost<EvalSuite>('/evals/suites', { name });

/** Copy a suite (usually a bundled one) into a new one you own and can edit. */
export const forkSuite = (id: string, name = '') =>
  apiPost<EvalSuite>(`/evals/suites/${id}/fork`, { name });

export const putCases = (id: string, cases: EvalCase[]) =>
  apiPut<{ suite: EvalSuite; cases: EvalCase[]; error: string }>(
    `/evals/suites/${id}/cases`,
    cases,
  );

export const deleteSuite = (id: string) => apiDelete(`/evals/suites/${id}`);

export const listCases = (id: string) =>
  apiGet<{ suite: EvalSuite; cases: EvalCase[]; error: string }>(`/evals/suites/${id}/cases`);

export const listRuns = (suiteId = '') =>
  apiGet<{ runs: EvalRun[] }>(
    `/evals/runs${suiteId ? `?suite_id=${encodeURIComponent(suiteId)}` : ''}`,
  ).then((r) => r.runs);

export const getRun = (runId: string) =>
  apiGet<{ run: EvalRun; results: CaseResult[] }>(`/evals/runs/${runId}`);

export const suggestTargets = () =>
  apiGet<{ targets: SuggestedTarget[] }>('/evals/targets').then((r) => r.targets);

export interface StartRunResult {
  started: boolean;
  key: string;
  message: string;
}

export const startRun = (body: {
  suite_id: string;
  targets: {
    provider?: string;
    endpoint?: string;
    model: string;
    model_path?: string;
    label?: string;
    /** Run this target on a peer instead, via a compute lease. */
    node?: string;
  }[];
  case_ids?: string[];
  localtrack_project?: string;
}) => apiPost<StartRunResult>('/evals/runs', body);

/** A sweep running on the node right now. */
export interface ActiveSweep {
  key: string;
  suiteId: string;
  /** The target labels it is working through. */
  targets: string[];
  startedAt: string;
}

/** What is running, asked of the node rather than remembered by the pane — a sweep
 *  outlives the pane that started it. */
export const listSweeps = () =>
  apiGet<{ sweeps: ActiveSweep[] }>('/evals/sweeps').then((r) => r.sweeps);

/** Stop a sweep. Targets it already finished keep their results. */
export const cancelSweep = (key: string) =>
  apiDelete<{ cancelled: boolean }>(`/evals/sweeps/${encodeURIComponent(key)}`);


// --- authoring a benchmark block --------------------------------------------

export interface DatasetPeek {
  dataset: string;
  config: string;
  split: string;
  columns: string[];
  rows: Record<string, unknown>[];
}

export interface ComparePreview {
  prompt: string;
  reference_raw: string;
  reference: string;
  reference_normalised: string;
  prediction: string;
  prediction_normalised: string;
  /** Everything wrong with this case, in the terms the run would fail in. */
  problems: string[];
}

export interface BenchmarkPreset {
  id: string;
  label: string;
  why: string;
  benchmark: Partial<HfBenchmark>;
}

export const datasetSplits = (dataset: string) =>
  apiGet<{ splits: { config: string; split: string }[] }>(
    `/evals/datasets/splits?dataset=${encodeURIComponent(dataset)}`,
  ).then((r) => r.splits);

export const peekDataset = (body: {
  dataset: string;
  config?: string;
  split?: string;
  limit?: number;
}) => apiPost<DatasetPeek>('/evals/datasets/peek', body);

export const comparePreview = (body: {
  row: Record<string, unknown>;
  input_template: string;
  target_column: string;
  target_regex: string;
  prediction_regex: string;
  sample_prediction?: string;
}) => apiPost<ComparePreview>('/evals/datasets/compare-preview', body);

export const benchmarkPresets = () =>
  apiGet<{ presets: BenchmarkPreset[] }>('/evals/datasets/presets').then((r) => r.presets);

/** Strip a `[:N]` slice off a split expression, for showing the bare split name. */
export function splitBase(split: string): string {
  return split.replace(/\[.*$/, '');
}


// --- comparing runs ----------------------------------------------------------

export interface BoardRun {
  id: string;
  label: string;
  model: string;
  provider: string;
  startedAt: string;
  attempted: number;
  passed: number;
  errored: number;
  rate: number;
  avgRounds: number;
  avgMs: number;
}

export interface BoardCase {
  caseId: string;
  /** run id → passed. A run missing from here did not attempt the case, which is
   * not the same as failing it. */
  verdicts: Record<string, boolean>;
  details: Record<string, string>;
  attempted: number;
  passes: number;
  /** The case's content changed between the runs shown, so the column is not a
   * like-for-like comparison. */
  edited: boolean;
  /** Nothing has ever passed it. Suspect the case before the models. */
  universalFailure: boolean;
  universalPass: boolean;
}

export interface Leaderboard {
  suite: EvalSuite;
  runs: BoardRun[];
  cases: BoardCase[];
  universalFailures: string[];
  editedCases: string[];
}

export interface RunDiff {
  base: { id: string; label: string };
  other: { id: string; label: string };
  shared: number;
  onlyInBase: string[];
  onlyInOther: string[];
  fixed: { caseId: string; detail: string }[];
  broken: { caseId: string; detail: string }[];
  changed: { caseId: string; before: boolean; after: boolean; detail: string }[];
  /** Either run hit a provider error on these. Not a regression — something else
   * broke — so they are kept out of `broken` where they would read as one. */
  errored: { caseId: string; detail: string }[];
  stillFailing: string[];
  /** Neither run recorded case hashes, so an edit cannot be ruled out. */
  hashesUnknown: boolean;
  /** Whether both runs saw the same tool catalog. A skill toggled or an MCP server
   *  started between two runs rewrites what there was to call, so a diff across one
   *  is not a diff of models. */
  harness: {
    /** Either run has no recorded harness. Not a weaker `differs` — nobody claimed
     *  they agree. */
    unknown: boolean;
    differs: boolean;
    base: string;
    other: string;
    /** Plain lines naming what changed. Empty unless `differs`. */
    changes: string[];
  };
}

export const getLeaderboard = (suiteId: string, limit = 8) =>
  apiGet<Leaderboard>(
    `/evals/leaderboard?suite_id=${encodeURIComponent(suiteId)}&limit=${limit}`,
  );

export const getDiff = (base: string, other: string) =>
  apiGet<RunDiff>(
    `/evals/leaderboard/diff?base=${encodeURIComponent(base)}&other=${encodeURIComponent(other)}`,
  );
