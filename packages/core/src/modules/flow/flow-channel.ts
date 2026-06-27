/**
 * The `flow` WebSocket channel: trigger a run on the backend executor and receive
 * live execution telemetry (node/edge events) so the canvas can light up. Tool
 * calls an Agent node makes ride the existing `agent` channel relay + permission
 * gate — this channel is telemetry only. See backend/modules/flow/executor.py.
 */
import { sendChannel, subscribeChannel, type WsMessage } from '../../ws';

export type FlowEventType =
  | 'node_started'
  | 'node_skipped'
  | 'node_reasoning'
  | 'node_token'
  | 'node_finished'
  | 'edge_fired'
  | 'run_finished'
  | 'error';

export interface FlowEvent {
  event: FlowEventType;
  data: {
    runId: string;
    nodeId?: string;
    edgeId?: string | null;
    from?: string;
    to?: string;
    delta?: string;
    ok?: boolean;
    output?: string;
    error?: string;
    message?: string;
  };
}

let counter = 0;

/** Trigger a flow run on the backend; returns the runId the events will carry. */
export function runFlow(flowId: string, input?: string): string {
  const runId = `${Date.now().toString(36)}-${(counter++).toString(36)}`;
  sendChannel('flow', 'run', { flowId, runId, input });
  return runId;
}

export function stopFlow(runId: string): void {
  sendChannel('flow', 'stop', { runId });
}

/** Subscribe to flow execution events. Returns an unsubscribe function. */
export function subscribeFlowEvents(cb: (evt: FlowEvent) => void): () => void {
  return subscribeChannel('flow', (msg: WsMessage) => {
    cb({ event: msg.event as FlowEventType, data: (msg.data ?? {}) as FlowEvent['data'] });
  });
}
