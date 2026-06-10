import { apiGet, apiPut } from './api';

/**
 * Opaque serialized workspace layout (the docking engine's own JSON shape).
 * Core treats it as a blob so the engine stays an implementation detail of
 * packages/ui — see docs/architecture/windowing.md.
 */
export type SerializedLayout = Record<string, unknown>;

export async function getWorkspaceLayout(): Promise<SerializedLayout | null> {
  const res = await apiGet<{ layout: SerializedLayout | null }>('/workspace/layout');
  return res.layout;
}

export function saveWorkspaceLayout(layout: SerializedLayout): Promise<unknown> {
  return apiPut('/workspace/layout', { layout });
}
