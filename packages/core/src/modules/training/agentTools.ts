/**
 * Frontend agent tools for the training notebook (group `training`). They act on
 * the session store — which drives the ws kernel protocol — so the agent can build
 * and debug the notebook alongside the user. Full cell CRUD + execute.
 *
 * Session resolution: an explicit `projectId` arg wins; otherwise the sole open
 * session is used (the common case), and ambiguity is reported so the agent picks.
 *
 * **These are `training.*`, not `notebook.*`.** They used to share the reactive
 * notebook's namespace while addressing a different store with a different session
 * arg (`projectId` vs `path`), and the orchestrator groups tools by name prefix —
 * so seven names existed twice, dispatch went to whichever module the registry
 * happened to reach first, and the loser was uncallable. The group they belong in
 * is `training`, which already exists and already has preload keywords.
 */
import type { AgentToolDecl, JSONSchema } from '@horribledashboard/sdk';

import { runAll, runCell } from './client';
import { getSession, listSessions, type SessionStore } from './store';
import type { NotebookCell } from './api';

function resolveSession(projectId?: string): SessionStore | { error: string } {
  if (projectId) {
    const s = getSession(String(projectId));
    return s ?? { error: `no open notebook session for project ${projectId}` };
  }
  const sessions = listSessions().filter((s) => s.snapshot().sessionKey !== null);
  if (sessions.length === 0) return { error: 'no open notebook — open a training notebook first' };
  if (sessions.length > 1) {
    return {
      error:
        'multiple notebooks open; pass projectId (one of: ' +
        sessions.map((s) => s.snapshot().projectId).join(', ') +
        ')',
    };
  }
  return sessions[0];
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

/** Wait for a cell to leave the running/queued state, then return its outputs. */
function waitForCell(store: SessionStore, cellId: string, timeoutMs = 120_000): Promise<unknown> {
  return new Promise((resolve) => {
    const done = () => {
      const state = store.snapshot().runStates[cellId];
      if (state === 'done' || state === 'error') {
        const cell = store.snapshot().cells.find((c) => c.id === cellId);
        resolve({
          cellId,
          state,
          outputs: cell?.outputs ?? [],
        });
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
      resolve({
        cellId,
        state: store.snapshot().runStates[cellId] ?? 'unknown',
        note: 'still running after 120s',
      });
    }, timeoutMs);
  });
}

const projectIdParam: Record<string, JSONSchema> = {
  projectId: {
    type: 'string',
    description: 'Project id (omit if only one notebook is open).',
  },
};

export const notebookAgentTools: AgentToolDecl[] = [
  {
    name: 'training.list_cells',
    description:
      'List the cells of the open training notebook (id, type, first line, execution count, run state, whether it errored).',
    params: { type: 'object', properties: { ...projectIdParam } },
    handler: (args) => {
      const s = resolveSession(args.projectId as string | undefined);
      if ('error' in s) return s;
      return { cells: s.snapshot().cells.map((c) => cellSummary(c, s)) };
    },
  },
  {
    name: 'training.read_cell',
    description:
      'Read a cell in full: its source and outputs (including any error traceback text).',
    params: {
      type: 'object',
      properties: { ...projectIdParam, cellId: { type: 'string' } },
      required: ['cellId'],
    },
    handler: (args) => {
      const s = resolveSession(args.projectId as string | undefined);
      if ('error' in s) return s;
      const cell = s.snapshot().cells.find((c) => c.id === String(args.cellId));
      if (!cell) return { error: `no cell ${String(args.cellId)}` };
      return { id: cell.id, type: cell.cell_type, source: cell.source, outputs: cell.outputs };
    },
  },
  {
    name: 'training.kernel_status',
    description: 'The kernel status of the open notebook (starting|idle|busy|restarting|dead).',
    params: { type: 'object', properties: { ...projectIdParam } },
    handler: (args) => {
      const s = resolveSession(args.projectId as string | undefined);
      if ('error' in s) return s;
      return { kernel: s.snapshot().kernel };
    },
  },
  {
    name: 'training.insert_cell',
    description: 'Insert a new cell. Position by afterCellId or index (default: end).',
    params: {
      type: 'object',
      properties: {
        ...projectIdParam,
        type: { type: 'string', description: 'code|markdown (default code).' },
        source: { type: 'string' },
        afterCellId: { type: 'string' },
        index: { type: 'integer' },
      },
    },
    sideEffect: true,
    specifierTemplate: '{projectId}',
    handler: (args) => {
      const s = resolveSession(args.projectId as string | undefined);
      if ('error' in s) return s;
      const type = (args.type as string) === 'markdown' ? 'markdown' : 'code';
      const cells = s.snapshot().cells;
      const temp: NotebookCell = {
        id: `tmp-${Date.now()}`,
        cell_type: type,
        source: String(args.source ?? ''),
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
            cellType: type,
            source: String(args.source ?? ''),
            afterCellId: args.afterCellId ? String(args.afterCellId) : undefined,
            index: args.afterCellId ? undefined : (args.index as number | undefined),
          },
        ],
        next,
      );
      return { ok: true, note: 'inserted (authoritative id arrives on next list_cells)' };
    },
  },
  {
    name: 'training.edit_cell',
    description: 'Replace the source of an existing cell.',
    params: {
      type: 'object',
      properties: { ...projectIdParam, cellId: { type: 'string' }, source: { type: 'string' } },
      required: ['cellId', 'source'],
    },
    sideEffect: true,
    specifierTemplate: '{projectId}',
    handler: (args) => {
      const s = resolveSession(args.projectId as string | undefined);
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
    name: 'training.delete_cell',
    description: 'Delete a cell by id.',
    params: {
      type: 'object',
      properties: { ...projectIdParam, cellId: { type: 'string' } },
      required: ['cellId'],
    },
    sideEffect: true,
    specifierTemplate: '{projectId}',
    handler: (args) => {
      const s = resolveSession(args.projectId as string | undefined);
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
    name: 'training.run_cell',
    description:
      'Run a code cell and wait for it to finish (up to 120s), returning its outputs and error traceback if any.',
    params: {
      type: 'object',
      properties: { ...projectIdParam, cellId: { type: 'string' } },
      required: ['cellId'],
    },
    sideEffect: true,
    specifierTemplate: '{projectId}',
    handler: async (args) => {
      const s = resolveSession(args.projectId as string | undefined);
      if ('error' in s) return s;
      const key = s.snapshot().sessionKey;
      if (!key) return { error: 'kernel not ready' };
      const cellId = String(args.cellId);
      runCell(key, cellId);
      return waitForCell(s, cellId);
    },
  },
  {
    name: 'training.run_all',
    description: 'Run every code cell in order.',
    params: { type: 'object', properties: { ...projectIdParam } },
    sideEffect: true,
    specifierTemplate: '{projectId}',
    handler: (args) => {
      const s = resolveSession(args.projectId as string | undefined);
      if ('error' in s) return s;
      const key = s.snapshot().sessionKey;
      if (!key) return { error: 'kernel not ready' };
      runAll(key);
      return { ok: true, note: 'running all cells; poll list_cells for state' };
    },
  },
  {
    name: 'training.interrupt',
    description: 'Interrupt the running kernel.',
    params: { type: 'object', properties: { ...projectIdParam } },
    sideEffect: true,
    specifierTemplate: '{projectId}',
    handler: async (args) => {
      const s = resolveSession(args.projectId as string | undefined);
      if ('error' in s) return s;
      const key = s.snapshot().sessionKey;
      if (!key) return { error: 'kernel not ready' };
      const { interruptKernel } = await import('./client');
      interruptKernel(key);
      return { ok: true };
    },
  },
  {
    name: 'training.restart',
    description: 'Restart the kernel (clears execution state).',
    params: { type: 'object', properties: { ...projectIdParam } },
    sideEffect: true,
    specifierTemplate: '{projectId}',
    handler: async (args) => {
      const s = resolveSession(args.projectId as string | undefined);
      if ('error' in s) return s;
      const key = s.snapshot().sessionKey;
      if (!key) return { error: 'kernel not ready' };
      const { restartKernel } = await import('./client');
      restartKernel(key);
      return { ok: true };
    },
  },
];
