import { apiDelete, apiGet, apiPost, apiPut } from './api';
import { apiUrl } from './origin';

/**
 * Opaque serialized workspace layout (the docking engine's own JSON shape).
 * Core treats it as a blob so the engine stays an implementation detail of
 * packages/ui — see docs/architecture/windowing.md.
 */
export type SerializedLayout = Record<string, unknown>;

/** One named workspace layout (frame blob). */
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

/**
 * Save a layout from a page that is going away (`pagehide` / tab hidden).
 *
 * `keepalive` is the whole point: a normal `fetch` started during unload is
 * cancelled along with the document, so the last edit before a refresh would be
 * lost — which is exactly what made a refresh look like it had discarded the
 * window you just opened. A keepalive request is handed to the browser and
 * delivered after the page is gone.
 *
 * Fire-and-forget by necessity: nothing is left to receive a response, so this
 * reports only whether the browser accepted the request. It is a backstop, never
 * the primary path — the debounced save still does the real work.
 *
 * `sendBeacon` would be the obvious tool and is the wrong one: it can only POST,
 * and this route is a PUT.
 */
export function saveWorkspaceOnUnload(id: string, layout: SerializedLayout): boolean {
  try {
    void fetch(apiUrl(`/api/workspaces/${encodeURIComponent(id)}`), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ layout }),
      keepalive: true,
    });
    return true;
  } catch {
    // Over the 64KB keepalive cap, or the browser refused it outright. The edit
    // stays unsaved rather than being marked saved — see the caller.
    return false;
  }
}

export function setActiveWorkspace(id: string): Promise<WorkspacesState> {
  return apiPut<WorkspacesState>('/workspaces/active', { id });
}

export function deleteWorkspace(id: string): Promise<WorkspacesState> {
  return apiDelete<WorkspacesState>(`/workspaces/${encodeURIComponent(id)}`);
}
