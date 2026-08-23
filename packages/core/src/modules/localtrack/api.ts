import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from '../../api';
import type { MetricSeries, PanelConfig, Project, Run, RunArtifact } from './types';

export async function fetchProjects(): Promise<Project[]> {
  const res = await apiGet<{ projects: Project[] }>('/localtrack/projects');
  return res.projects;
}

export async function createProject(name: string, description = ''): Promise<Project> {
  return await apiPost<Project>('/localtrack/projects', { name, description });
}

export async function deleteProject(id: string): Promise<void> {
  await apiDelete(`/localtrack/projects/${encodeURIComponent(id)}`);
}

export async function fetchRuns(projectId?: string): Promise<Run[]> {
  const url = projectId
    ? `/localtrack/runs?project_id=${encodeURIComponent(projectId)}`
    : '/localtrack/runs';
  const res = await apiGet<{ runs: Run[] }>(url);
  return res.runs;
}

export async function fetchRun(runId: string): Promise<Run> {
  return await apiGet<Run>(`/localtrack/runs/${encodeURIComponent(runId)}`);
}

export async function updateRun(runId: string, updates: Partial<Run>): Promise<Run> {
  return await apiPatch<Run>(`/localtrack/runs/${encodeURIComponent(runId)}`, updates);
}

export async function deleteRun(runId: string): Promise<void> {
  await apiDelete(`/localtrack/runs/${encodeURIComponent(runId)}`);
}

export async function fetchMetricKeys(projectId?: string, runIds?: string[]): Promise<string[]> {
  let url = '/localtrack/metrics/keys';
  const params: string[] = [];
  if (projectId) params.push(`project_id=${encodeURIComponent(projectId)}`);
  if (runIds && runIds.length > 0) params.push(`run_ids=${encodeURIComponent(runIds.join(','))}`);
  if (params.length > 0) url += `?${params.join('&')}`;

  const res = await apiGet<{ keys: string[] }>(url);
  return res.keys;
}

export async function queryMetrics(
  runIds: string[],
  keys: string[],
  maxPoints = 500,
  smoothing = 0.0
): Promise<MetricSeries[]> {
  if (!runIds.length || !keys.length) return [];
  const res = await apiPost<{ series: MetricSeries[] }>('/localtrack/metrics/query', {
    run_ids: runIds,
    keys,
    max_points: maxPoints,
    smoothing,
  });
  return res.series;
}

export async function fetchRunArtifacts(runId: string): Promise<RunArtifact[]> {
  const res = await apiGet<{ artifacts: RunArtifact[] }>(
    `/localtrack/runs/${encodeURIComponent(runId)}/artifacts`
  );
  return res.artifacts;
}

/**
 * The saved panel arrangement for a project.
 *
 * `null` means the project has never saved one (use the defaults); `[]` means the
 * user removed every panel. Two different answers — collapsing them would make a
 * deliberately cleared workspace spring back to the four default charts on every
 * reload.
 */
export async function fetchLayout(projectId: string): Promise<PanelConfig[] | null> {
  const res = await apiGet<{ panels: PanelConfig[] | null }>(
    `/localtrack/projects/${encodeURIComponent(projectId)}/layout`,
  );
  return res.panels;
}

export async function saveLayout(projectId: string, panels: PanelConfig[]): Promise<void> {
  await apiPut(`/localtrack/projects/${encodeURIComponent(projectId)}/layout`, { panels });
}
