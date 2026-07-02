/**
 * Typed client for the training backend (`/api/training/*`). Shared by the
 * training panes and (later) the module's agent tools.
 */
import { apiDelete, apiGet, apiPost, apiPut } from '../../api';

export type EnvironmentKind = 'competition' | 'dataset' | 'env';

export interface EnvironmentRef {
  provider: string;
  kind: EnvironmentKind;
  id: string;
  title: string;
  url?: string | null;
  meta: Record<string, unknown>;
}

export interface ProviderInfo {
  provider: string;
  label: string;
  kinds: EnvironmentKind[];
}

export interface Project {
  id: string;
  name: string;
  root: string;
  refs: EnvironmentRef[];
  python: string;
  venv_ready: boolean;
  data_ready: boolean;
  created_at: string;
}

export interface NotebookCell {
  id: string;
  cell_type: 'code' | 'markdown';
  source: string;
  outputs: Record<string, unknown>[];
  execution_count?: number | null;
}

export interface Notebook {
  path: string;
  cells: NotebookCell[];
  metadata: Record<string, unknown>;
}

export function listProviders(): Promise<{ providers: ProviderInfo[] }> {
  return apiGet('/training/providers');
}

export function searchEnvironments(
  provider: string,
  q: string,
  kind?: EnvironmentKind,
): Promise<{ results: EnvironmentRef[] }> {
  const params = new URLSearchParams({ q });
  if (kind) params.set('kind', kind);
  return apiGet(`/training/providers/${encodeURIComponent(provider)}/search?${params}`);
}

export function resolveEnvironment(
  provider: string,
  id: string,
  kind?: EnvironmentKind,
): Promise<EnvironmentRef> {
  return apiPost(`/training/providers/${encodeURIComponent(provider)}/resolve`, { id, kind });
}

export function listProjects(): Promise<{ projects: Project[] }> {
  return apiGet('/training/projects');
}

export function getProject(id: string): Promise<Project> {
  return apiGet(`/training/projects/${encodeURIComponent(id)}`);
}

export function createProject(input: {
  provider: string;
  ref: string;
  kind?: EnvironmentKind;
  name?: string;
}): Promise<Project> {
  return apiPost('/training/projects', input);
}

export function deleteProject(id: string): Promise<{ deleted: boolean }> {
  return apiDelete(`/training/projects/${encodeURIComponent(id)}`);
}

export function fetchProjectData(id: string): Promise<{ status: string; detail: string }> {
  return apiPost(`/training/projects/${encodeURIComponent(id)}/fetch`, {});
}

export function installDeps(
  id: string,
  packages: string[],
): Promise<{ status: string; detail: string }> {
  return apiPost(`/training/projects/${encodeURIComponent(id)}/deps`, { packages });
}

export function getNotebook(id: string, path = 'main.ipynb'): Promise<Notebook> {
  const params = new URLSearchParams({ path });
  return apiGet(`/training/projects/${encodeURIComponent(id)}/notebook?${params}`);
}

export function putNotebook(id: string, notebook: Notebook): Promise<Notebook> {
  return apiPut(`/training/projects/${encodeURIComponent(id)}/notebook`, notebook);
}

export interface PushResult {
  target: string;
  url?: string | null;
  status: string;
  detail: string;
}

export function pushProject(id: string, target: string): Promise<PushResult> {
  return apiPost(`/training/projects/${encodeURIComponent(id)}/push/${target}`, {});
}

export function googleStatus(): Promise<{ connected: boolean }> {
  return apiGet('/training/google/status');
}

export function googleAuthStart(): Promise<{ authUrl: string }> {
  return apiPost('/training/google/auth/start', {});
}

export function googleAuthComplete(code: string): Promise<{ ok: boolean }> {
  return apiPost('/training/google/auth/complete', { code });
}

export interface TrainingAd {
  node_id: string;
  node_name: string;
  status: 'offering' | 'seeking' | 'none';
  specs: {
    platform?: string;
    cpu?: string;
    cpu_count?: number;
    ram_gb?: number;
    gpu?: string | null;
    vram_gb?: number | null;
  };
  note: string;
  ts: number;
}

export function listAds(): Promise<{ ads: TrainingAd[] }> {
  return apiGet('/training/fabric/ads');
}

export function advertise(
  status: 'off' | 'offering' | 'seeking',
  note?: string,
): Promise<{ status: string }> {
  return apiPost('/training/fabric/advertise', { status, note });
}
