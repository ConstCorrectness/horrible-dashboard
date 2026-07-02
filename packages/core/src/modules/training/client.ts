/**
 * WS bridge for the `training` channel: typed event payloads, a
 * subscribe-by-event helper, and the kernel-protocol senders.
 */
import { sendChannel, subscribeChannel } from '../../ws';

import type { Notebook, Project } from './api';

export interface ProgressEvent {
  projectId: string;
  line: string;
  pct?: number;
}

export type KernelStatus = 'starting' | 'idle' | 'busy' | 'restarting' | 'dead';
export type CellRunState = 'queued' | 'running' | 'done' | 'error';

/** nbformat output dict, kept raw (mirrors the backend). */
export type NbOutput = Record<string, unknown>;

export interface TrainingEventMap {
  env_progress: ProgressEvent;
  fetch_progress: ProgressEvent;
  project_changed: Project;
  opened: { sessionKey: string; projectId: string; notebook: Notebook; kernel: KernelStatus };
  kernel_status: { sessionKey: string; status: KernelStatus };
  execution_state: {
    sessionKey: string;
    cellId: string;
    state: CellRunState;
    execCount?: number | null;
  };
  output: { sessionKey: string; cellId: string; output: NbOutput | null };
  cells_changed: { sessionKey: string; notebook: Notebook };
  metrics: {
    projectId: string;
    runId?: string;
    step?: number | null;
    values: Record<string, number>;
    ts: number;
  };
  run_started: { projectId: string; runId: string; name: string };
  run_backfill: { runId: string; points: TrainingEventMap['metrics'][]; runs: string[] };
  frame: { projectId: string; source?: string; dataUrl: string };
  model_graph: { projectId: string; graph: ModelGraph };
  model_stats: { projectId: string; stats: Record<string, { w_norm: number; g_norm: number }> };
  error: { sessionKey?: string; message: string };
}

export interface ModelGraphNode {
  id: string;
  name: string;
  op: string;
  params: number;
  shape?: number[] | null;
}

export interface ModelGraph {
  kind: 'fx' | 'modules';
  nodes: ModelGraphNode[];
  edges: { from: string; to: string }[];
}

export type TrainingEvent = keyof TrainingEventMap;

/** Subscribe to one training event; returns the unsubscriber. */
export function onTrainingEvent<E extends TrainingEvent>(
  event: E,
  handler: (data: TrainingEventMap[E]) => void,
): () => void {
  return subscribeChannel('training', (msg) => {
    if (msg.event === event && msg.data != null) {
      handler(msg.data as TrainingEventMap[E]);
    }
  });
}

// --- kernel protocol senders -------------------------------------------------

export function openNotebook(projectId: string, notebook = 'main.ipynb'): void {
  sendChannel('training', 'open', { projectId, notebook });
}

export function runCell(sessionKey: string, cellId: string): void {
  sendChannel('training', 'run_cell', { sessionKey, cellId });
}

export function runAll(sessionKey: string): void {
  sendChannel('training', 'run_all', { sessionKey });
}

export interface CellOp {
  op: 'insert' | 'edit' | 'delete' | 'move';
  cellId?: string;
  source?: string;
  cellType?: 'code' | 'markdown';
  afterCellId?: string;
  index?: number;
}

export function sendCellOps(sessionKey: string, ops: CellOp[]): void {
  sendChannel('training', 'cells', { sessionKey, ops });
}

export function interruptKernel(sessionKey: string): void {
  sendChannel('training', 'interrupt', { sessionKey });
}

export function restartKernel(sessionKey: string): void {
  sendChannel('training', 'restart', { sessionKey });
}

export function shutdownKernel(sessionKey: string): void {
  sendChannel('training', 'shutdown', { sessionKey });
}

export function watchRun(runId: string): void {
  sendChannel('training', 'watch_run', { runId });
}
