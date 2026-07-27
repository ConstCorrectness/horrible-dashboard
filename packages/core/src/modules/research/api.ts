/**
 * Typed client for the research backend (`/api/research/*`, `/api/artifacts/*`).
 *
 * Backs the PDF/Page viewers and the research commands, so panes and agent tools
 * reach the backend through `packages/core` without knowing route shapes.
 */
import { apiGet, apiPost } from '../../api';
import { apiUrl } from '../../origin';
import type { SourceModel } from '../library/api';

export type ArtifactKind = 'pdf' | 'page' | 'report';

export interface ArtifactModel {
  id: string;
  sha256: string;
  kind: ArtifactKind;
  mime: string;
  filename: string;
  size: number;
  origin_url?: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface CaptureResponse {
  artifact: ArtifactModel;
  source: SourceModel;
}

/** The byte URL a viewer loads an artifact from (iframe src, pdf.js input). */
export function artifactUrl(artifactId: string): string {
  return apiUrl(`/api/artifacts/${artifactId}`);
}

export function artifactMeta(artifactId: string): Promise<ArtifactModel> {
  return apiGet<ArtifactModel>(`/artifacts/${artifactId}/meta`);
}

/** Save a URL as a self-contained page artifact + library source (no engine needed). */
export function captureUrl(
  url: string,
  opts: { library?: string; title?: string; tags?: string[] } = {},
): Promise<CaptureResponse> {
  return apiPost<CaptureResponse>('/research/capture', { url, ...opts });
}

/** Fetch a PDF by URL into the artifact store + library. */
export function savePdfUrl(
  url: string,
  opts: { library?: string; title?: string; tags?: string[] } = {},
): Promise<CaptureResponse> {
  return apiPost<CaptureResponse>('/research/pdf', { url, ...opts });
}

/** Upload a local PDF file into the artifact store, then file it as a source. */
export async function uploadPdf(
  file: File,
  opts: { library?: string; tags?: string[] } = {},
): Promise<CaptureResponse> {
  const form = new FormData();
  form.append('file', file, file.name);
  const res = await fetch(apiUrl('/api/artifacts/upload'), { method: 'POST', body: form });
  if (!res.ok) {
    let detail = `upload failed: ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // non-JSON error body — keep the status message
    }
    throw new Error(detail);
  }
  const { artifact } = (await res.json()) as { artifact: ArtifactModel };
  const source = await apiPost<SourceModel>('/library/sources', {
    type: 'pdf',
    artifact_id: artifact.id,
    library: opts.library,
    tags: opts.tags,
  });
  return { artifact, source };
}

export interface ExportResponse {
  note_path: string;
  attachment_path?: string | null;
}

// --- deep-research runs -----------------------------------------------------

export type RunStatus =
  | 'pending'
  | 'planning'
  /** Parked after planning, waiting for the user to approve or edit the plan. */
  | 'awaiting_plan'
  | 'researching'
  | 'synthesizing'
  | 'verifying'
  | 'citing'
  | 'exporting'
  | 'done'
  | 'failed'
  | 'cancelled';

export type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

export interface SubagentSpec {
  name: string;
  objective: string;
  output_format: string;
  tool_guidance: string;
  boundaries: string;
  max_tool_calls: number;
}

export interface RunModel {
  id: string;
  query: string;
  status: RunStatus;
  effort: string;
  library: string;
  provider?: string | null;
  model?: string | null;
  plan?: { complexity: string; subagents: SubagentSpec[] } | null;
  report_artifact_id?: string | null;
  report_source_id?: string | null;
  error?: string | null;
  tokens_used: number;
  token_budget: number;
  cancel_requested: boolean;
  /** 'plan' parks the run at the approval gate; 'auto' runs straight through. */
  approval_mode: string;
  rounds_used: number;
  created_at: string;
  updated_at: string;
}

export interface StepModel {
  id: string;
  run_id: string;
  seq: number;
  kind: 'plan' | 'subagent' | 'critique' | 'synthesis' | 'verify' | 'citations' | 'export';
  name: string;
  status: StepStatus;
  attempt: number;
  max_attempts: number;
  /** Which gap-filling wave this step belongs to (0 for the first pass). */
  round: number;
  input: Record<string, unknown>;
  output?: Record<string, unknown> | null;
  transcript?: { role: string; content?: string }[] | null;
  tokens_used: number;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface ToolCallModel {
  id: string;
  run_id: string;
  step_id: string;
  seq: number;
  name: string;
  args: Record<string, unknown>;
  ok: boolean;
  ms?: number | null;
  summary: string;
  created_at?: string | null;
}

export interface FollowupModel {
  id: string;
  run_id: string;
  text: string;
  created_at?: string | null;
  consumed_at?: string | null;
}

export function startRun(args: {
  query: string;
  effort?: string;
  library?: string;
  provider?: string;
  model?: string;
  approval_mode?: 'auto' | 'plan';
}): Promise<RunModel> {
  return apiPost<RunModel>('/research/runs', args);
}

/**
 * Release a run parked at the approval gate. Omitting `plan` approves the lead's
 * proposal unchanged; passing one replaces it (validated server-side).
 */
export function approvePlan(
  runId: string,
  plan?: { complexity: string; subagents: SubagentSpec[] },
): Promise<RunModel> {
  return apiPost<RunModel>(`/research/runs/${runId}/plan`, plan ? { plan } : {});
}

/** Ask a running investigation something extra; it shapes the next round. */
export function addFollowup(runId: string, text: string): Promise<FollowupModel> {
  return apiPost<FollowupModel>(`/research/runs/${runId}/followup`, { text });
}

export function getToolCalls(runId: string): Promise<{ calls: ToolCallModel[] }> {
  return apiGet<{ calls: ToolCallModel[] }>(`/research/runs/${runId}/tool-calls`);
}

export function listRuns(): Promise<{ runs: RunModel[] }> {
  return apiGet<{ runs: RunModel[] }>('/research/runs');
}

export function getRunSteps(
  runId: string,
  opts: { transcript?: boolean } = {},
): Promise<{ steps: StepModel[] }> {
  const suffix = opts.transcript ? '?transcript=true' : '';
  return apiGet<{ steps: StepModel[] }>(`/research/runs/${runId}/steps${suffix}`);
}

export function cancelRun(runId: string): Promise<RunModel> {
  return apiPost<RunModel>(`/research/runs/${runId}/cancel`, {});
}

export function retryRun(runId: string): Promise<RunModel> {
  return apiPost<RunModel>(`/research/runs/${runId}/retry`, {});
}

/** Export a stored source into the configured Obsidian vault. */
export function exportToObsidian(args: {
  source_id?: string;
  artifact_id?: string;
}): Promise<ExportResponse> {
  return apiPost<ExportResponse>('/research/export', args);
}
