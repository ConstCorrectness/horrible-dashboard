import { apiDelete, apiGet, apiPost, apiPut } from './api';

/**
 * Opaque serialized workspace layout (the docking engine's own JSON shape).
 * Core treats it as a blob so the engine stays an implementation detail of
 * packages/ui — see docs/architecture/windowing.md.
 */
export type SerializedLayout = Record<string, unknown>;

/** One named dockview layout. */
export interface Workspace {
  id: string;
  name: string;
  layout: SerializedLayout | null;
}

/** The whole collection plus which workspace is active. */
export interface WorkspacesState {
  active: string | null;
  workspaces: Workspace[];
}

export function getWorkspaces(): Promise<WorkspacesState> {
  return apiGet<WorkspacesState>('/workspaces');
}

/** Create a workspace with a generated id; becomes active if it's the first. */
export function createWorkspace(name: string): Promise<Workspace> {
  return apiPost<Workspace>('/workspaces', { name });
}

/**
 * Upsert a workspace by id. Only the fields passed are applied, so saving a
 * layout never clobbers the name and vice-versa. The Dashboard is seeded with
 * the stable id `dashboard` via this call.
 */
export function saveWorkspace(
  id: string,
  patch: { name?: string; layout?: SerializedLayout },
): Promise<Workspace> {
  return apiPut<Workspace>(`/workspaces/${encodeURIComponent(id)}`, patch);
}

export function setActiveWorkspace(id: string): Promise<WorkspacesState> {
  return apiPut<WorkspacesState>('/workspaces/active', { id });
}

export function deleteWorkspace(id: string): Promise<WorkspacesState> {
  return apiDelete<WorkspacesState>(`/workspaces/${encodeURIComponent(id)}`);
}
