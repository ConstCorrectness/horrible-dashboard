/**
 * Flow store client: CRUD for saved orchestration graphs over `/api/flows`.
 * Mirrors the workspace store (packages/core/src/workspace.ts) — the backend keeps
 * each flow's nodes/edges opaque; the executor reads them at run time. See
 * docs/modules/flow-canvas.md.
 */
import { apiDelete, apiGet, apiPost, apiPut } from '../../api';

/** A node's runtime config (model, system prompt, …) — shape depends on `type`. */
export type FlowNodeConfig = Record<string, unknown>;

export interface FlowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  config: FlowNodeConfig;
}

export interface FlowEdge {
  id?: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

export interface Flow {
  id: string;
  name: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
}

export interface FlowsState {
  active: string | null;
  flows: Flow[];
}

export function getFlows(): Promise<FlowsState> {
  return apiGet<FlowsState>('/flows');
}

export function getFlow(id: string): Promise<Flow> {
  return apiGet<Flow>(`/flows/${encodeURIComponent(id)}`);
}

/** Create a flow with a generated id; becomes active. */
export function createFlow(name: string): Promise<Flow> {
  return apiPost<Flow>('/flows', { name });
}

/**
 * Upsert a flow by id. Only the fields passed are applied, so saving the graph
 * never clobbers the name and vice-versa.
 */
export function saveFlow(
  id: string,
  patch: { name?: string; nodes?: FlowNode[]; edges?: FlowEdge[] },
): Promise<Flow> {
  return apiPut<Flow>(`/flows/${encodeURIComponent(id)}`, patch);
}

export function deleteFlow(id: string): Promise<FlowsState> {
  return apiDelete<FlowsState>(`/flows/${encodeURIComponent(id)}`);
}
