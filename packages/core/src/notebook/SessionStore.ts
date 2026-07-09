/**
 * Generic notebook-session store: one external store per backend session key,
 * fed by a kernel `/ws` channel, read by a pane via `useSyncExternalStore`. The
 * store is keyed by the *deterministic* backend session key so it can be
 * registered before `opened` arrives (and matched by error/opened events).
 */
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

export interface SessionState {
  sessionKey: string | null; // null until `opened` arrives
  id: string; // deterministic backend session key
  cells: NotebookCell[];
  kernel: KernelStatus;
  mode: ExecutionMode;
  runStates: Record<string, CellRunState>;
  edges: DependencyEdge[]; // reactive dependency DAG
  diagnostics: CellDiagnostic[]; // multiple_defs / cycle / syntax
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
  comms: [],
  error: null,
  errorCode: null,
});

export class SessionStore {
  private state: SessionState;
  private listeners = new Set<() => void>();
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
    this.set({ runStates, cells });
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
          text: String(last.text ?? '') + String(output.text ?? ''),
        };
      } else {
        outputs.push(output);
      }
      return { ...c, outputs };
    });
    this.set({ cells });
  }

  onCellsChanged(notebook: Notebook): void {
    this.set({ cells: notebook.cells, mode: readMode(notebook) });
  }

  onError(message: string, code?: string): void {
    this.set({ error: message, errorCode: code ?? null });
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
    this.set({ cells: optimistic });
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
