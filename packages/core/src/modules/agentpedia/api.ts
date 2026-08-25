/**
 * HTTP client for agentpedia.
 *
 * Types mirror `backend/modules/agentpedia/models.py`, which is the source of
 * truth — a field added there has to be added here *and* to the Pydantic response
 * model, or it never reaches the browser.
 *
 * Field names are snake_case because the backend models are: agentpedia joins
 * three stores that already disagree about casing (`agent_turns` is camelCase over
 * the wire, `traj_runs` is snake), and picking one at the seam beat translating
 * halfway. The one exception is `shown`, which is interpretability's own
 * `RoundSnapshot` verbatim — camelCase, because renaming it would mean the stepper
 * and the context pane no longer render the same object.
 *
 * The Harness section reads `/api/trajectories/*` directly rather than through the
 * trajectories module's client. An HTTP route is a public surface; another module's
 * `api.ts` is an internal.
 */
import { apiGet } from '../../api';
import type { RoundView as ShownRound } from '../../ContextBlocks';

export type WireStatus = 'live' | 'aged_out' | 'unrecorded';

export interface WireEvent {
  id: number;
  ts: number;
  source: string;
  method: string;
  target: string;
  status: number | null;
  duration_ms: number | null;
  request_bytes: number | null;
  response_bytes: number | null;
  request_body: string | null;
  response_body: string | null;
  error: string | null;
}

export interface DidStep {
  seq: number;
  kind: string;
  name: string | null;
  ok: boolean | null;
  gated: boolean;
  duration_ms: number | null;
  tokens: number | null;
  error: string | null;
  content: string | null;
  args: unknown;
  result: unknown;
}

export interface FlattenReport {
  messages_in: number;
  messages_out: number;
  merged: string[];
}

export interface RoundCost {
  message_tokens: number;
  tool_tokens: number;
  total_tokens: number;
  window: number | null;
  window_pct: number | null;
  wall_ms: number | null;
}

export interface RoundView {
  round: number;
  shown: ShownRound;
  wire: WireEvent[];
  did: DidStep[];
  cost: RoundCost;
  flatten: FlattenReport;
}

export interface RunLink {
  id: string;
  dataset_id: string;
  status: string;
  outcome: string | null;
  goal: string;
  steps: number;
  harness: string | null;
  duration_ms: number | null;
}

export interface TurnView {
  turn_id: string;
  parent_turn_id: string | null;
  agent_id: string;
  agent_name: string;
  kind: string;
  peer_id: string | null;
  model: string;
  provider: string;
  started_at: number;
  exact: boolean;
  tokenizer_repo: string | null;
  tokenizer_source: string;
  requested_num_ctx: number | null;
  model_context_length: number | null;
  temperature: number | null;
  rounds: RoundView[];
  run: RunLink | null;
  wire_status: WireStatus;
}

export interface TurnIndexEntry {
  turn_id: string;
  parent_turn_id: string | null;
  agent_id: string;
  agent_name: string;
  kind: string;
  model: string;
  provider: string;
  started_at: number;
  rounds: number;
  total_tokens: number;
  run: RunLink | null;
}

export interface TurnIndex {
  turns: TurnIndexEntry[];
  capture_on: boolean;
}

export function listTurns(limit = 100, rootsOnly = false): Promise<TurnIndex> {
  return apiGet<TurnIndex>(`/agentpedia/turns?limit=${limit}&roots_only=${rootsOnly}`);
}

export function getTurn(turnId: string): Promise<TurnView> {
  return apiGet<TurnView>(`/agentpedia/turns/${encodeURIComponent(turnId)}`);
}

// ── The harness pages, served by trajectories ────────────────────────────────

export interface Harness {
  fingerprint: string;
  agent_id: string;
  model: string;
  provider: string;
  system_prompt: string;
  tool_names: string[];
  params: Record<string, unknown>;
  first_seen: number;
  last_seen: number;
  run_count: number;
  label: string;
}

export interface ToolStat {
  name: string;
  calls: number;
  /** Errored. Kept apart from `gated` on purpose — a broken tool and a harness
   *  that refuses the call are completely different findings. */
  failures: number;
  gated: number;
  avg_ms: number | null;
}

export function listHarnesses(limit = 100): Promise<{ harnesses: Harness[] }> {
  return apiGet<{ harnesses: Harness[] }>(`/trajectories/harnesses?limit=${limit}`);
}

export function harnessTools(fingerprint: string): Promise<{ tools: ToolStat[] }> {
  return apiGet<{ tools: ToolStat[] }>(
    `/trajectories/tools?harness=${encodeURIComponent(fingerprint)}&limit=50`,
  );
}
