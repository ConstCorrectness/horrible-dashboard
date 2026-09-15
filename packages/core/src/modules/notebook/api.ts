/**
 * Typed client for the notebook backend (`/api/notebook/*`): the file catalog,
 * creating notebooks, loading a document, the execution-mode flag, and publishing.
 * Cell execution rides the `notebook` ws channel (see the shared kernel client).
 */
import { apiDelete, apiGet, apiPost, apiPut } from '../../api';
import type { Notebook } from '../../notebook/types';
import type { ExecutionMode } from '../../notebook/SessionStore';

export interface NotebookFile {
  path: string;
  name: string;
  modified: number;
}

export interface NotebookList {
  root: string;
  files: NotebookFile[];
}

export function listNotebooks(): Promise<NotebookList> {
  return apiGet('/notebook/files');
}

export function getNotebookDoc(path: string): Promise<Notebook> {
  return apiGet(`/notebook/doc?path=${encodeURIComponent(path)}`);
}

export function createNotebook(path: string, mode: ExecutionMode = 'reactive'): Promise<Notebook> {
  return apiPost('/notebook', { path, mode });
}

export function setNotebookMode(path: string, mode: ExecutionMode): Promise<Notebook> {
  return apiPut('/notebook/mode', { path, mode });
}

/** Mirrors `EnvLibrariesOut` in `backend/modules/notebook/models.py`. */
export interface NotebookLibraries {
  state: 'ready' | 'idle' | 'installing' | 'failed' | 'unmanaged';
  missing: string[];
  installing: string[];
  /** The latest installer output line. */
  line: string;
  error: string;
  /** Where PyTorch comes from when it is missing; empty means PyPI. */
  torch_index: string;
}

export interface NotebookEnv {
  ready: boolean;
  libraries: NotebookLibraries;
}

export function envStatus(): Promise<NotebookEnv> {
  return apiGet('/notebook/env');
}

/** Start, or retry, installing the configured libraries into the notebook venv. */
export function installLibraries(): Promise<NotebookEnv> {
  return apiPost('/notebook/env/install', {});
}

// --- publishing --------------------------------------------------------------
// Mirrors the publish models in `backend/modules/notebook/models.py`.

export type PublishVisibility = 'secret' | 'public';

/** Something in the prepared copy worth seeing before it leaves this machine. */
export interface PublishFinding {
  cell: number;
  cell_id: string;
  where: 'source' | 'output';
  kind: 'secret' | 'path' | 'traceback' | 'widget';
  label: string;
  /** For a secret, only its first characters. */
  excerpt: string;
  /** True for secrets: publishing waits for an explicit confirmation. */
  blocking: boolean;
}

export interface Publication {
  path: string;
  target: string;
  remote_id: string;
  url: string;
  extra_url: string;
  visibility: PublishVisibility;
  published_at: number;
  detail: Record<string, unknown>;
}

export interface PublishTarget {
  id: string;
  label: string;
  visibilities: PublishVisibility[];
  available: boolean;
  /** What to do when it is not available. */
  reason: string;
  /** False where the destination has no delete API — offer Forget instead. */
  can_unpublish: boolean;
}

export interface PublishOut {
  ok: boolean;
  publication: Publication | null;
  note: string;
  error: string;
  findings: PublishFinding[];
}

export function getPublications(
  path: string,
): Promise<{ publications: Publication[]; targets: PublishTarget[] }> {
  return apiGet(`/notebook/publish?path=${encodeURIComponent(path)}`);
}

export function preflightPublish(
  path: string,
  stripOutputs: boolean,
): Promise<{ findings: PublishFinding[]; blocking: number }> {
  return apiPost('/notebook/publish/preflight', { path, strip_outputs: stripOutputs });
}

export function publishNotebook(options: {
  path: string;
  target: string;
  visibility: PublishVisibility;
  stripOutputs: boolean;
  acknowledged: boolean;
}): Promise<PublishOut> {
  return apiPost('/notebook/publish', {
    path: options.path,
    target: options.target,
    visibility: options.visibility,
    strip_outputs: options.stripOutputs,
    acknowledged: options.acknowledged,
  });
}

export function unpublishNotebook(
  path: string,
  target: string,
  forget = false,
): Promise<{ ok: boolean; error: string }> {
  const query = new URLSearchParams({ path, target, forget: String(forget) });
  return apiDelete(`/notebook/publish?${query.toString()}`);
}

export function saveNotebookHtml(path: string, stripOutputs: boolean): Promise<{ path: string }> {
  return apiPost('/notebook/publish/html', { path, strip_outputs: stripOutputs });
}
