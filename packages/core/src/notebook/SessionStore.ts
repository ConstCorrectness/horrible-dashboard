/**
 * Generic notebook-session store: one external store per backend session key,
 * fed by a kernel `/ws` channel, read by a pane via `useSyncExternalStore`. The
 * store is keyed by the *deterministic* backend session key so it can be
 * registered before `opened` arrives (and matched by error/opened events).
 */
import { collapseCarriageReturns } from './streamText';
import { useSyncExternalStore } from 'react';

import {
  onKernelEvent,
  runAll as sendRunAll,
  runCell as sendRunCell,
  sendCellOps,
  sendOpen,
  setMode as sendSetMode,
  type CellDiagnostic,
  type CommSnapshot,
} from './kernelClient';
import type { CellOp, CellRunState, KernelStatus, NbOutput, Notebook, NotebookCell } from './types';

export type ExecutionMode = 'reactive' | 'classic';

export interface DependencyEdge {
  from: string;
  to: string;
}

/** A completed execution retained for the notebook's local-session timeline. */
export interface ExecutionRecord {
  cellId: string;
  state: Extract<CellRunState, 'done' | 'error'>;
  startedAt: number;
  finishedAt: number;
}

const EXECUTION_HISTORY_LIMIT = 80;

export interface SessionState {
  sessionKey: string | null; // null until `opened` arrives
  id: string; // deterministic backend session key
  cells: NotebookCell[];
  kernel: KernelStatus;
  mode: ExecutionMode;
  runStates: Record<string, CellRunState>;
  edges: DependencyEdge[]; // reactive dependency DAG
  diagnostics: CellDiagnostic[]; // multiple_defs / cycle / syntax
  /** Cells whose source or upstream values changed after their last successful run. */
  staleCells: string[];
  /** Most recent completed executions. Deliberately session-local, not notebook metadata. */
  executionHistory: ExecutionRecord[];
  comms: CommSnapshot[]; // live widget comms, for reattach-resync
  error: string | null;
  errorCode: string | null;
}

function readMode(notebook: Notebook): ExecutionMode {
  const horrible = (notebook.metadata?.horrible ?? {}) as Record<string, unknown>;
  return horrible.execution_mode === 'classic' ? 'classic' : 'reactive';
}

const EMPTY = (id: string): SessionState => ({
  sessionKey: null,
  id,
  cells: [],
  kernel: 'starting',
  mode: 'reactive',
  runStates: {},
  edges: [],
  diagnostics: [],
  staleCells: [],
  executionHistory: [],
  comms: [],
  error: null,
  errorCode: null,
});

export class SessionStore {
  private state: SessionState;
  private listeners = new Set<() => void>();
  private executionStartedAt = new Map<string, number>();
  readonly id: string; // === the backend session key
  readonly channel: string;

  constructor(channel: string, id: string) {
    this.channel = channel;
    this.id = id;
    this.state = EMPTY(id);
  }

  snapshot = (): SessionState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private set(patch: Partial<SessionState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((l) => l());
  }

  // --- ws ingestion ---------------------------------------------------------

  onOpened(notebook: Notebook, kernel: KernelStatus, comms?: CommSnapshot[]): void {
    this.set({
      sessionKey: this.id,
      cells: notebook.cells,
      kernel,
      mode: readMode(notebook),
      comms: comms ?? [],
      error: null,
      errorCode: null,
    });
  }

  onKernelStatus(status: KernelStatus): void {
    this.set({ kernel: status });
  }

  onExecutionState(cellId: string, state: CellRunState, execCount?: number | null): void {
    const runStates = { ...this.state.runStates, [cellId]: state };
    let cells = this.state.cells;
    if (state === 'queued') {
      // The backend clears outputs when a cell is (re)queued — mirror it.
      cells = cells.map((c) =>
        c.id === cellId ? { ...c, outputs: [], execution_count: null } : c,
      );
    } else if (execCount != null) {
      cells = cells.map((c) => (c.id === cellId ? { ...c, execution_count: execCount } : c));
    }
    const stale = new Set(this.state.staleCells);
    let executionHistory = this.state.executionHistory;
    if (state === 'queued') {
      // A queued cell will receive new values, so it and every dependent remain
      // stale until their own executions complete.
      stale.add(cellId);
    } else if (state === 'done' || state === 'error') {
      stale.delete(cellId);
      const startedAt = this.executionStartedAt.get(cellId) ?? Date.now();
      executionHistory = [
        { cellId, state, startedAt, finishedAt: Date.now() },
        ...executionHistory,
      ].slice(0, EXECUTION_HISTORY_LIMIT);
      this.executionStartedAt.delete(cellId);
    } else if (state === 'running') {
      this.executionStartedAt.set(cellId, Date.now());
    }
    this.set({ runStates, cells, staleCells: [...stale], executionHistory });
  }

  onOutput(cellId: string, output: NbOutput | null): void {
    const cells = this.state.cells.map((c) => {
      if (c.id !== cellId) return c;
      if (output === null) return { ...c, outputs: [] };
      const outputs = [...c.outputs];
      const last = outputs[outputs.length - 1];
      // Mirror the backend's stream merging so text accumulates in one block.
      if (
        output.output_type === 'stream' &&
        last?.output_type === 'stream' &&
        last.name === output.name
      ) {
        outputs[outputs.length - 1] = {
          ...last,
          // Same rule as the backend's merge — see `collapseCarriageReturns`.
          text: collapseCarriageReturns(String(last.text ?? '') + String(output.text ?? '')),
        };
      } else {
        outputs.push(output);
      }
      return { ...c, outputs };
    });
    this.set({ cells });
  }

  /** Replace one output in place — a `display_id` update, e.g. the HF Trainer's
   *  progress table, which would otherwise freeze on its first frame. */
  onOutputUpdated(cellId: string, index: number, output: NbOutput): void {
    const cells = this.state.cells.map((c) => {
      if (c.id !== cellId || index < 0 || index >= c.outputs.length) return c;
      const outputs = [...c.outputs];
      outputs[index] = output;
      return { ...c, outputs };
    });
    this.set({ cells });
  }

  onCellsChanged(notebook: Notebook): void {
    this.set({ cells: notebook.cells, mode: readMode(notebook) });
  }

  onError(message: string, code?: string): void {
    // An error arriving before `opened` is the **open** failing, so the kernel badge
    // has to move with it. `kernel` starts at `starting` and only `opened` or a
    // `kernel_status` event ever changed it, which left a failed open showing
    // "● starting" *beside* its own error message.
    //
    // Deliberately only that case: the same event also carries recoverable errors on
    // a live session (a rejected `cells` op), and calling the kernel dead for one of
    // those would be a different lie.
    const openFailed = this.state.sessionKey === null;
    this.set({
      error: message,
      errorCode: code ?? null,
      ...(openFailed ? { kernel: 'dead' as const } : {}),
    });
  }

  onMode(mode: ExecutionMode): void {
    this.set({ mode });
  }

  onGraph(edges: DependencyEdge[], diagnostics: CellDiagnostic[]): void {
    this.set({ edges, diagnostics });
  }

  /** Optimistically flip mode and tell the live kernel session (rebuilds its graph). */
  setMode(mode: ExecutionMode): void {
    this.set({ mode });
    if (this.state.sessionKey) sendSetMode(this.channel, this.state.sessionKey, mode);
  }

  // --- commands -------------------------------------------------------------

  run(cellId: string): void {
    if (this.state.sessionKey) sendRunCell(this.channel, this.state.sessionKey, cellId);
  }

  runAll(): void {
    if (this.state.sessionKey) sendRunAll(this.channel, this.state.sessionKey);
  }

  // --- local mutations (optimistic; backend doc is authoritative) -----------

  applyLocal(ops: CellOp[], optimistic: NotebookCell[]): void {
    const changed = new Set(
      ops
        .filter((op) => op.op === 'edit' || op.op === 'delete')
        .flatMap((op) => (op.cellId ? [op.cellId] : [])),
    );
    const stale = new Set(this.state.staleCells);
    // Mark every transitive dependent stale immediately. The backend will rebuild
    // the graph after the edit; using the last known graph here is intentional: it
    // tells the truth without waiting for the websocket round-trip.
    const downstream = new Map<string, string[]>();
    for (const edge of this.state.edges) {
      const next = downstream.get(edge.from) ?? [];
      next.push(edge.to);
      downstream.set(edge.from, next);
    }
    const pending = [...changed];
    while (pending.length) {
      const id = pending.pop();
      if (!id || stale.has(id)) continue;
      stale.add(id);
      pending.push(...(downstream.get(id) ?? []));
    }
    this.set({ cells: optimistic, staleCells: [...stale] });
    if (this.state.sessionKey) sendCellOps(this.channel, this.state.sessionKey, ops);
  }
}

const stores = new Map<string, SessionStore>();
const wired = new Set<string>();

function wireChannel(channel: string): void {
  if (wired.has(channel)) return;
  wired.add(channel);
  onKernelEvent(channel, 'opened', (d) =>
    stores.get(d.sessionKey)?.onOpened(d.notebook, d.kernel, d.comms),
  );
  onKernelEvent(channel, 'kernel_status', (d) =>
    stores.get(d.sessionKey)?.onKernelStatus(d.status),
  );
  onKernelEvent(channel, 'execution_state', (d) =>
    stores.get(d.sessionKey)?.onExecutionState(d.cellId, d.state, d.execCount),
  );
  onKernelEvent(channel, 'output', (d) => stores.get(d.sessionKey)?.onOutput(d.cellId, d.output));
  onKernelEvent(channel, 'output_updated', (d) =>
    stores.get(d.sessionKey)?.onOutputUpdated(d.cellId, d.index, d.output),
  );
  onKernelEvent(channel, 'cells_changed', (d) =>
    stores.get(d.sessionKey)?.onCellsChanged(d.notebook),
  );
  onKernelEvent(channel, 'mode', (d) => stores.get(d.sessionKey)?.onMode(d.mode));
  onKernelEvent(channel, 'graph', (d) => stores.get(d.sessionKey)?.onGraph(d.edges, d.diagnostics));
  onKernelEvent(channel, 'error', (d) => {
    if (d.sessionKey) stores.get(d.sessionKey)?.onError(d.message, d.code);
  });
}

/**
 * Get (or create) the store for `key` on `channel` and ask the backend to open
 * it. `key` is the deterministic backend session key; `openData` is the module's
 * open payload (e.g. `{ path }` for notebook, `{ projectId, notebook }` for
 * training).
 */
export function openSession(
  channel: string,
  key: string,
  openData: Record<string, unknown>,
): SessionStore {
  wireChannel(channel);
  let store = stores.get(key);
  if (!store) {
    store = new SessionStore(channel, key);
    stores.set(key, store);
  }
  sendOpen(channel, openData);
  return store;
}

export function getSession(key: string): SessionStore | undefined {
  return stores.get(key);
}

export function listSessions(channel?: string): SessionStore[] {
  const all = [...stores.values()];
  return channel ? all.filter((s) => s.channel === channel) : all;
}

export function useSession(store: SessionStore): SessionState {
  return useSyncExternalStore(store.subscribe, store.snapshot);
}
