/**
 * Frontend agent tools for the reactive notebook (group `notebook`). They act on
 * the session store — which drives the ws kernel protocol — so the agent can build,
 * run, and debug a notebook alongside the user. Cell CRUD, execute, and mode.
 *
 * Session resolution: an explicit `path` arg wins; otherwise the sole open notebook
 * is used (the common case), and ambiguity is reported so the agent picks.
 */
import type { AgentToolDecl, JSONSchema } from '@horribledashboard/sdk';

import {
  getSession,
  listSessions,
  type ExecutionMode,
  type SessionStore,
} from '../../notebook/SessionStore';
import type { CellRunState, NotebookCell } from '../../notebook/types';
import { NOTEBOOK_CHANNEL, sessionKeyFor } from './store';

function resolveSession(path?: string): SessionStore | { error: string } {
  if (path) {
    const s = getSession(sessionKeyFor(String(path)));
    return s ?? { error: `no open notebook for ${path}` };
  }
  const open = listSessions(NOTEBOOK_CHANNEL).filter((s) => s.snapshot().sessionKey !== null);
  if (open.length === 0) return { error: 'no open notebook — open one first' };
  if (open.length > 1) {
    return {
      error:
        'multiple notebooks open; pass path (one of: ' +
        open.map((s) => s.snapshot().id.replace(/^nb:/, '')).join(', ') +
        ')',
    };
  }
  return open[0];
}

function cellSummary(cell: NotebookCell, store: SessionStore) {
  const st = store.snapshot().runStates[cell.id];
  return {
    id: cell.id,
    type: cell.cell_type,
    firstLine: cell.source.split('\n', 1)[0] ?? '',
    execCount: cell.execution_count ?? null,
    state: st ?? null,
    hasError: cell.outputs.some((o) => o.output_type === 'error'),
  };
}

/** Wait for a cell (and, in reactive mode, its dependents) to settle. */
function waitForCell(store: SessionStore, cellId: string, timeoutMs = 120_000): Promise<unknown> {
  return new Promise((resolve) => {
    const settled = (id: string): CellRunState | undefined => store.snapshot().runStates[id];
    const done = () => {
      const state = settled(cellId);
      if (state === 'done' || state === 'error') {
        const cell = store.snapshot().cells.find((c) => c.id === cellId);
        resolve({ cellId, state, outputs: cell?.outputs ?? [] });
        return true;
      }
      return false;
    };
    if (done()) return;
    const unsub = store.subscribe(() => {
      if (done()) unsub();
    });
    setTimeout(() => {
      unsub();
      resolve({ cellId, state: settled(cellId) ?? 'unknown', note: 'still running after 120s' });
    }, timeoutMs);
  });
}

const pathParam: Record<string, JSONSchema> = {
  path: { type: 'string', description: 'Notebook path (omit if only one is open).' },
};

export const notebookAgentTools: AgentToolDecl[] = [
  {
    name: 'nb.list_cells',
    description:
      'List the cells of the open notebook (id, type, first line, execution count, run state, whether it errored).',
    params: { type: 'object', properties: { ...pathParam } },
    handler: (args) => {
      const s = resolveSession(args.path as string | undefined);
      if ('error' in s) return s;
      const snap = s.snapshot();
      return {
        mode: snap.mode,
        kernel: snap.kernel,
        cells: snap.cells.map((c) => cellSummary(c, s)),
        diagnostics: snap.diagnostics,
      };
    },
  },
  {
    name: 'notebook.read_cell',
    description:
      'Read a cell in full: its source and outputs (including any error traceback text).',
    params: {
      type: 'object',
      properties: { ...pathParam, cellId: { type: 'string' } },
      required: ['cellId'],
    },
    handler: (args) => {
      const s = resolveSession(args.path as string | undefined);
      if ('error' in s) return s;
      const cell = s.snapshot().cells.find((c) => c.id === String(args.cellId));
      if (!cell) return { error: `no cell ${String(args.cellId)}` };
      return { id: cell.id, type: cell.cell_type, source: cell.source, outputs: cell.outputs };
    },
  },
  {
    name: 'notebook.kernel_status',
    description: 'Kernel status + execution mode of the open notebook.',
    params: { type: 'object', properties: { ...pathParam } },
    handler: (args) => {
      const s = resolveSession(args.path as string | undefined);
      if ('error' in s) return s;
      return { kernel: s.snapshot().kernel, mode: s.snapshot().mode };
    },
  },
  {
    name: 'notebook.insert_cell',
    description:
      'Insert a new cell (returns its id). Position by afterCellId or index (default: end).',
    params: {
      type: 'object',
      properties: {
        ...pathParam,
        type: { type: 'string', description: 'code|markdown (default code).' },
        source: { type: 'string' },
        afterCellId: { type: 'string' },
        index: { type: 'integer' },
      },
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: (args) => {
      const s = resolveSession(args.path as string | undefined);
      if ('error' in s) return s;
      const type = (args.type as string) === 'markdown' ? 'markdown' : 'code';
      const id = crypto.randomUUID();
      const source = String(args.source ?? '');
      const cells = s.snapshot().cells;
      const temp: NotebookCell = {
        id,
        cell_type: type,
        source,
        outputs: [],
        execution_count: null,
      };
      let at = cells.length;
      if (args.afterCellId) {
        const i = cells.findIndex((c) => c.id === String(args.afterCellId));
        if (i >= 0) at = i + 1;
      } else if (typeof args.index === 'number') {
        at = Math.max(0, Math.min(args.index, cells.length));
      }
      const next = [...cells];
      next.splice(at, 0, temp);
      s.applyLocal(
        [
          {
            op: 'insert',
            cellId: id,
            cellType: type,
            source,
            afterCellId: args.afterCellId ? String(args.afterCellId) : undefined,
            index: args.afterCellId ? undefined : (args.index as number | undefined),
          },
        ],
        next,
      );
      return { ok: true, cellId: id };
    },
  },
  {
    name: 'notebook.edit_cell',
    description: 'Replace the source of an existing cell.',
    params: {
      type: 'object',
      properties: { ...pathParam, cellId: { type: 'string' }, source: { type: 'string' } },
      required: ['cellId', 'source'],
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: (args) => {
      const s = resolveSession(args.path as string | undefined);
      if ('error' in s) return s;
      const cellId = String(args.cellId);
      const cells = s.snapshot().cells;
      if (!cells.some((c) => c.id === cellId)) return { error: `no cell ${cellId}` };
      s.applyLocal(
        [{ op: 'edit', cellId, source: String(args.source) }],
        cells.map((c) => (c.id === cellId ? { ...c, source: String(args.source) } : c)),
      );
      return { ok: true };
    },
  },
  {
    name: 'notebook.delete_cell',
    description: 'Delete a cell by id (reactive mode: drops its variables and re-runs dependents).',
    params: {
      type: 'object',
      properties: { ...pathParam, cellId: { type: 'string' } },
      required: ['cellId'],
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: (args) => {
      const s = resolveSession(args.path as string | undefined);
      if ('error' in s) return s;
      const cellId = String(args.cellId);
      s.applyLocal(
        [{ op: 'delete', cellId }],
        s.snapshot().cells.filter((c) => c.id !== cellId),
      );
      return { ok: true };
    },
  },
  {
    name: 'notebook.run_cell',
    description:
      'Run a code cell and wait for it (and, in reactive mode, its dependents) to finish, returning its outputs.',
    params: {
      type: 'object',
      properties: { ...pathParam, cellId: { type: 'string' } },
      required: ['cellId'],
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: async (args) => {
      const s = resolveSession(args.path as string | undefined);
      if ('error' in s) return s;
      if (!s.snapshot().sessionKey) return { error: 'kernel not ready' };
      const cellId = String(args.cellId);
      s.run(cellId);
      return waitForCell(s, cellId);
    },
  },
  {
    name: 'notebook.run_all',
    description: 'Run every code cell (reactive mode: in dependency order).',
    params: { type: 'object', properties: { ...pathParam } },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: (args) => {
      const s = resolveSession(args.path as string | undefined);
      if ('error' in s) return s;
      if (!s.snapshot().sessionKey) return { error: 'kernel not ready' };
      s.runAll();
      return { ok: true, note: 'running; poll list_cells for state' };
    },
  },
  {
    name: 'notebook.set_mode',
    description: 'Set the execution mode: reactive (auto-rerun dependents) or classic (linear).',
    params: {
      type: 'object',
      properties: { ...pathParam, mode: { type: 'string', description: 'reactive|classic' } },
      required: ['mode'],
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: (args) => {
      const s = resolveSession(args.path as string | undefined);
      if ('error' in s) return s;
      const mode = String(args.mode) === 'classic' ? 'classic' : 'reactive';
      s.setMode(mode as ExecutionMode);
      return { ok: true, mode };
    },
  },
];
