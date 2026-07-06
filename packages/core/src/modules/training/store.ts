/**
 * Notebook-session store: one external store per `sessionKey`, fed by the
 * `training` ws channel, read by the NotebookPane via `useSyncExternalStore`
 * (and later by the module's agent tools — the store is the single seam that
 * talks to the backend, so panes and tools stay in lockstep).
 */
import { useSyncExternalStore } from 'react';

import type { Notebook, NotebookCell } from './api';
import {
  onTrainingEvent,
  openNotebook,
  sendCellOps,
  type CellOp,
  type CellRunState,
  type KernelStatus,
  type NbOutput,
} from './client';

export interface SessionState {
  sessionKey: string | null; // null until `opened` arrives
  projectId: string;
  notebookPath: string;
  cells: NotebookCell[];
  kernel: KernelStatus;
  runStates: Record<string, CellRunState>;
  error: string | null;
  errorCode: string | null; // e.g. 'unknown_project' — lets the pane self-heal
}

const EMPTY = (projectId: string, notebookPath: string): SessionState => ({
  sessionKey: null,
  projectId,
  notebookPath,
  cells: [],
  kernel: 'starting',
  runStates: {},
  error: null,
  errorCode: null,
});

export class SessionStore {
  private state: SessionState;
  private listeners = new Set<() => void>();
  readonly id: string; // `${projectId}:${notebookPath}` — matches the backend key

  constructor(projectId: string, notebookPath: string) {
    this.id = `${projectId}:${notebookPath}`;
    this.state = EMPTY(projectId, notebookPath);
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

  onOpened(notebook: Notebook, kernel: KernelStatus): void {
    this.set({
      sessionKey: this.id,
      cells: notebook.cells,
      kernel,
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
    this.set({ cells: notebook.cells });
  }

  onError(message: string, code?: string): void {
    this.set({ error: message, errorCode: code ?? null });
  }

  // --- local mutations (optimistic; backend doc is authoritative) -----------

  applyLocal(ops: CellOp[], optimistic: NotebookCell[]): void {
    this.set({ cells: optimistic });
    if (this.state.sessionKey) sendCellOps(this.state.sessionKey, ops);
  }
}

const stores = new Map<string, SessionStore>();
let wired = false;

function wireChannel(): void {
  if (wired) return;
  wired = true;
  onTrainingEvent('opened', (d) => {
    stores.get(d.sessionKey)?.onOpened(d.notebook, d.kernel);
  });
  onTrainingEvent('kernel_status', (d) => stores.get(d.sessionKey)?.onKernelStatus(d.status));
  onTrainingEvent('execution_state', (d) =>
    stores.get(d.sessionKey)?.onExecutionState(d.cellId, d.state, d.execCount),
  );
  onTrainingEvent('output', (d) => stores.get(d.sessionKey)?.onOutput(d.cellId, d.output));
  onTrainingEvent('cells_changed', (d) => stores.get(d.sessionKey)?.onCellsChanged(d.notebook));
  onTrainingEvent('error', (d) => {
    if (d.sessionKey) stores.get(d.sessionKey)?.onError(d.message, d.code);
  });
}

/** Get (or create) the store for a notebook session and ask the backend to open it. */
export function openSession(projectId: string, notebookPath = 'main.ipynb'): SessionStore {
  wireChannel();
  const key = `${projectId}:${notebookPath}`;
  let store = stores.get(key);
  if (!store) {
    store = new SessionStore(projectId, notebookPath);
    stores.set(key, store);
  }
  if (projectId) {
    openNotebook(projectId, notebookPath);
  }
  return store;
}

export function getSession(
  projectId: string,
  notebookPath = 'main.ipynb',
): SessionStore | undefined {
  return stores.get(`${projectId}:${notebookPath}`);
}

/** All live sessions (agent tools pick the active pane's, or the only one). */
export function listSessions(): SessionStore[] {
  return [...stores.values()];
}

export function useSession(store: SessionStore): SessionState {
  return useSyncExternalStore(store.subscribe, store.snapshot);
}
