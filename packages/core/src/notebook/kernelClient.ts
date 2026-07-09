/**
 * Generic kernel-protocol client over a named `/ws` channel. The event map and
 * senders are the domain-neutral kernel slice both the `notebook` and `training`
 * channels speak; a module binds them to its channel name.
 */
import { sendChannel, subscribeChannel } from '../ws';

import type { CellOp, CellRunState, KernelStatus, NbOutput, Notebook } from './types';

export interface KernelEventMap {
  opened: {
    sessionKey: string;
    notebook: Notebook;
    kernel: KernelStatus;
    path?: string;
    /** Live widget comms for reattach-resync (empty on a fresh kernel). */
    comms?: CommSnapshot[];
  };
  kernel_status: { sessionKey: string; status: KernelStatus };
  execution_state: {
    sessionKey: string;
    cellId: string;
    state: CellRunState;
    execCount?: number | null;
  };
  output: { sessionKey: string; cellId: string; output: NbOutput | null };
  cells_changed: { sessionKey: string; notebook: Notebook };
  mode: { sessionKey: string; mode: 'reactive' | 'classic' };
  graph: {
    sessionKey: string;
    edges: { from: string; to: string }[];
    defs: Record<string, string[]>;
    diagnostics: CellDiagnostic[];
  };
  comm_open: { sessionKey: string; comm: CommContent; buffers: string[] };
  comm_msg: { sessionKey: string; comm: CommContent; buffers: string[] };
  comm_close: { sessionKey: string; comm: CommContent; buffers: string[] };
  error: { sessionKey?: string; message: string; code?: string };
}

/** Raw Jupyter comm message content (ipywidgets rides this). */
export interface CommContent {
  comm_id: string;
  target_name?: string;
  data?: { method?: string; state?: Record<string, unknown>; [k: string]: unknown };
}

/** A widget comm's last-known state, sent on `opened` for reattach-resync. */
export interface CommSnapshot {
  comm_id: string;
  target_name?: string;
  state: Record<string, unknown>;
}

export interface CellDiagnostic {
  cellId: string;
  kind: 'multiple_defs' | 'cycle' | 'syntax';
  message: string;
  names: string[];
}

export type KernelEvent = keyof KernelEventMap;

/** Subscribe to one kernel event on `channel`; returns the unsubscriber. */
export function onKernelEvent<E extends KernelEvent>(
  channel: string,
  event: E,
  handler: (data: KernelEventMap[E]) => void,
): () => void {
  return subscribeChannel(channel, (msg) => {
    if (msg.event === event && msg.data != null) {
      handler(msg.data as KernelEventMap[E]);
    }
  });
}

// --- kernel protocol senders (channel-parametrized) --------------------------

export function sendOpen(channel: string, data: Record<string, unknown>): void {
  sendChannel(channel, 'open', data);
}

export function runCell(channel: string, sessionKey: string, cellId: string): void {
  sendChannel(channel, 'run_cell', { sessionKey, cellId });
}

export function runAll(channel: string, sessionKey: string): void {
  sendChannel(channel, 'run_all', { sessionKey });
}

export function setMode(channel: string, sessionKey: string, mode: 'reactive' | 'classic'): void {
  sendChannel(channel, 'set_mode', { sessionKey, mode });
}

export function sendCellOps(channel: string, sessionKey: string, ops: CellOp[]): void {
  sendChannel(channel, 'cells', { sessionKey, ops });
}

export function interruptKernel(channel: string, sessionKey: string): void {
  sendChannel(channel, 'interrupt', { sessionKey });
}

export function restartKernel(channel: string, sessionKey: string): void {
  sendChannel(channel, 'restart', { sessionKey });
}

export function shutdownKernel(channel: string, sessionKey: string): void {
  sendChannel(channel, 'shutdown', { sessionKey });
}

/** Send a widget state update / custom event back to the kernel comm. */
export function sendCommMsg(
  channel: string,
  sessionKey: string,
  commId: string,
  data: Record<string, unknown>,
): void {
  sendChannel(channel, 'comm_msg', { sessionKey, commId, data });
}
