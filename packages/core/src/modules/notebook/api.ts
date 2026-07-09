/**
 * Typed client for the notebook backend (`/api/notebook/*`): the file catalog,
 * creating notebooks, loading a document, and the execution-mode flag. Cell
 * execution rides the `notebook` ws channel (see the shared kernel client).
 */
import { apiGet, apiPost, apiPut } from '../../api';
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

export function envStatus(): Promise<{ ready: boolean }> {
  return apiGet('/notebook/env');
}
