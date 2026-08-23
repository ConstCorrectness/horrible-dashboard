/**
 * Typed client for the training backend (`/api/training/*`). Shared by the
 * training panes and (later) the module's agent tools.
 */
import { apiDelete, apiGet, apiPost, apiPut, streamNdjson } from '../../api';

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
  /** The module that owns this project as working storage (`evals` builds one per
   * benchmark suite). Empty means it is yours. An owned project has no scaffolded
   * notebook and a venv without `ipykernel`, so the authoring actions are marked
   * and disabled rather than hidden. */
  owner?: string;
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

// --- the recipe surface -------------------------------------------------------

/** One knob in the recipe form, as the backend catalog declares it. */
export interface RecipeField {
  name: string;
  target: 'sft' | 'lora';
  label: string;
  type: 'int' | 'float' | 'bool' | 'text' | 'select';
  default: unknown;
  help: string;
  group: string;
  options: string[];
  aliases: string[];
}

/**
 * How a field will be emitted against the library that is actually installed.
 *
 * The four states are not decoration: `unsupported` means the field is left out
 * of the generated code entirely, while `unvalidated` means nobody could ask and
 * it is emitted hopefully. Rendering them the same way would hide which of those
 * happened.
 */
export interface ResolvedField {
  name: string;
  emit: string | null;
  status: 'ok' | 'renamed' | 'unsupported' | 'unvalidated';
  note: string;
}

export interface Introspection {
  available: boolean;
  versions: Record<string, string>;
  accepted: Record<string, string[]>;
  /** Fields the installed class accepts that this form does not render. */
  extra: Record<string, number>;
  error: string;
}

export interface Recipe {
  task: string;
  baseModel: string;
  dataset: string;
  datasetSplit: string;
  textField: string;
  useLora: boolean;
  outputDir: string;
  trackers: string[];
  values: Record<string, unknown>;
}

export interface RecipePayload {
  recipe: Recipe;
  fields: RecipeField[];
  introspection: Introspection;
  resolved: ResolvedField[];
  warnings: string[];
  trackers: string[];
  tasks: string[];
  outputTypes: string[];
}

export interface Checkpoint {
  path: string;
  relPath: string;
  /** `lora` is an adapter: converted with a different script, served with --lora. */
  kind: 'model' | 'lora';
  sizeBytes: number;
  modified: number;
}

export interface DocLink {
  label: string;
  url: string;
  title: string;
  version: string | null;
  installedMismatch: string | null;
}

export function getRecipe(projectId: string, refresh = false): Promise<RecipePayload> {
  return apiGet(`/training/projects/${projectId}/recipe${refresh ? '?refresh=true' : ''}`);
}

export function saveRecipe(projectId: string, recipe: Recipe): Promise<RecipePayload> {
  return apiPut(`/training/projects/${projectId}/recipe`, recipe);
}

export function applyRecipe(
  projectId: string,
  recipe: Recipe,
): Promise<{ cells: number; notebook: string }> {
  return apiPost(`/training/projects/${projectId}/recipe/apply`, recipe);
}

export function recipeDocs(): Promise<{ links: DocLink[] }> {
  return apiGet('/training/recipe/docs');
}

export function listCheckpoints(
  projectId: string,
): Promise<{ checkpoints: Checkpoint[]; note: string }> {
  return apiGet(`/training/projects/${projectId}/checkpoints`);
}

export function convertCheckpoint(
  projectId: string,
  body: { checkpoint: string; outType: string; baseModel?: string },
  onProgress: (event: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson(
    `/training/projects/${projectId}/convert`,
    body,
    (obj) => onProgress(obj as Record<string, unknown>),
    signal,
  );
}
