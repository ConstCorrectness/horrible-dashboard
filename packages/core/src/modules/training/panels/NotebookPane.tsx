import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { NotebookCell } from '../api';
import {
  interruptKernel,
  restartKernel,
  runAll,
  runCell,
  type CellOp,
  type CellRunState,
} from '../client';
import { openSession, useSession } from '../store';
import { OutputRenderer } from '../outputs/OutputRenderer';
import { CellEditor } from './CellEditor';
import { useAgentContext } from '../../../agent-context';
import { usePaneParams } from '../../../panes';

const dim = { color: 'var(--text-dim)' } as const;

const STATE_BADGE: Record<CellRunState, string> = {
  queued: '⏳',
  running: '▶',
  done: '✓',
  error: '✗',
};

const EDIT_SYNC_MS = 400;

/**
 * The native notebook pane: cells on a per-project Jupyter kernel. Opened with
 * params `{projectId, notebook?}`; non-singleton so several notebooks can sit
 * side by side. The kernel session is process-global backend-side — closing the
 * pane leaves training running; reopening reattaches.
 */
export function NotebookPane() {
  const params = usePaneParams();
  const projectId = String(params.projectId ?? '');
  const notebookPath = String(params.notebook ?? 'main.ipynb');
  const store = useMemo(() => openSession(projectId, notebookPath), [projectId, notebookPath]);
  const state = useSession(store);
  const editTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  useAgentContext(() => ({
    projectId,
    notebook: notebookPath,
    kernel: state.kernel,
    lastError: state.error,
    cells: state.cells.map((c) => ({
      id: c.id,
      type: c.cell_type,
      firstLine: c.source.split('\n', 1)[0] ?? '',
      state: state.runStates[c.id] ?? null,
      hasError: c.outputs.some((o) => o.output_type === 'error'),
    })),
  }));

  useEffect(
    () => () => {
      editTimers.current.forEach((t) => clearTimeout(t));
    },
    [],
  );

  const sessionKey = state.sessionKey;

  const syncEdit = useCallback(
    (cellId: string, source: string) => {
      // Optimistic local update now, debounced authoritative ws op after.
      const optimistic = store
        .snapshot()
        .cells.map((c) => (c.id === cellId ? { ...c, source } : c));
      const timers = editTimers.current;
      const prior = timers.get(cellId);
      if (prior) clearTimeout(prior);
      timers.set(
        cellId,
        setTimeout(() => {
          timers.delete(cellId);
          store.applyLocal([{ op: 'edit', cellId, source }], store.snapshot().cells);
        }, EDIT_SYNC_MS),
      );
      // Local echo without an op (the debounced op carries the final text).
      store.onCellsChanged({ path: notebookPath, cells: optimistic, metadata: {} });
    },
    [store, notebookPath],
  );

  const flushEdits = useCallback(
    (cellId: string) => {
      const timer = editTimers.current.get(cellId);
      if (timer) {
        clearTimeout(timer);
        editTimers.current.delete(cellId);
        const cell = store.snapshot().cells.find((c) => c.id === cellId);
        if (cell)
          store.applyLocal([{ op: 'edit', cellId, source: cell.source }], store.snapshot().cells);
      }
    },
    [store],
  );

  const run = useCallback(
    (cellId: string) => {
      if (!sessionKey) return;
      flushEdits(cellId);
      runCell(sessionKey, cellId);
    },
    [sessionKey, flushEdits],
  );

  const mutate = useCallback(
    (ops: CellOp[], next: NotebookCell[]) => store.applyLocal(ops, next),
    [store],
  );

  if (!projectId) {
    return (
      <div style={{ padding: '1rem', ...dim }}>
        No project — open me from the Training projects pane.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.25rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.75rem',
        }}
      >
        <strong>{projectId}</strong>
        <span style={dim}>{notebookPath}</span>
        <span style={{ flex: 1 }} />
        <span
          title="Kernel status"
          style={{
            color:
              state.kernel === 'idle'
                ? 'var(--ok, #57ab5a)'
                : state.kernel === 'dead'
                  ? 'var(--danger, #e5534b)'
                  : 'var(--text-dim)',
          }}
        >
          ● {state.kernel}
        </span>
        <button disabled={!sessionKey} onClick={() => sessionKey && runAll(sessionKey)}>
          Run all
        </button>
        <button disabled={!sessionKey} onClick={() => sessionKey && interruptKernel(sessionKey)}>
          Interrupt
        </button>
        <button disabled={!sessionKey} onClick={() => sessionKey && restartKernel(sessionKey)}>
          Restart
        </button>
      </div>
      {state.error && (
        <div
          style={{ padding: '0.3rem 0.5rem', color: 'var(--danger, #e5534b)', fontSize: '0.75rem' }}
        >
          {state.error}
        </div>
      )}
      <div style={{ flex: 1, overflow: 'auto', padding: '0.5rem' }}>
        {!sessionKey && !state.error && (
          <div style={{ fontSize: '0.8rem', ...dim }}>Starting kernel…</div>
        )}
        {state.cells.map((cell, i) => (
          <Cell
            key={cell.id}
            cell={cell}
            runState={state.runStates[cell.id]}
            onChange={(src) => syncEdit(cell.id, src)}
            onRun={() => run(cell.id)}
            onDelete={() =>
              mutate(
                [{ op: 'delete', cellId: cell.id }],
                state.cells.filter((c) => c.id !== cell.id),
              )
            }
            onAddBelow={(type) => {
              // The authoritative id comes back via cells_changed; a temp id keeps
              // React keyed until then.
              const temp: NotebookCell = {
                id: `tmp-${Date.now()}`,
                cell_type: type,
                source: '',
                outputs: [],
                execution_count: null,
              };
              const next = [...state.cells];
              next.splice(i + 1, 0, temp);
              mutate([{ op: 'insert', afterCellId: cell.id, cellType: type, source: '' }], next);
            }}
          />
        ))}
        {state.cells.length === 0 && sessionKey && (
          <button
            onClick={() => mutate([{ op: 'insert', cellType: 'code', source: '' }], state.cells)}
          >
            + Add first cell
          </button>
        )}
      </div>
    </div>
  );
}

function Cell({
  cell,
  runState,
  onChange,
  onRun,
  onDelete,
  onAddBelow,
}: {
  cell: NotebookCell;
  runState?: CellRunState;
  onChange: (source: string) => void;
  onRun: () => void;
  onDelete: () => void;
  onAddBelow: (type: 'code' | 'markdown') => void;
}) {
  const [hover, setHover] = useState(false);
  const isCode = cell.cell_type === 'code';
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        gap: '0.4rem',
        marginBottom: '0.5rem',
        border: '1px solid var(--border)',
        borderLeft: `3px solid ${
          runState === 'error'
            ? 'var(--danger, #e5534b)'
            : runState === 'running' || runState === 'queued'
              ? 'var(--accent, #539bf5)'
              : 'var(--border)'
        }`,
        borderRadius: 4,
        padding: '0.25rem 0.4rem',
      }}
    >
      <div style={{ width: '2.6rem', textAlign: 'right', fontSize: '0.7rem', ...dim }}>
        {isCode ? (
          <>
            <div>[{cell.execution_count ?? ' '}]</div>
            <div>{runState ? STATE_BADGE[runState] : ''}</div>
          </>
        ) : (
          'md'
        )}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <CellEditor
          value={cell.source}
          language={isCode ? 'python' : 'markdown'}
          onChange={onChange}
          onRun={onRun}
        />
        {cell.outputs.length > 0 && (
          <div
            style={{
              borderTop: '1px dashed var(--border)',
              marginTop: '0.25rem',
              paddingTop: '0.25rem',
            }}
          >
            {cell.outputs.map((o, idx) => (
              <OutputRenderer key={idx} output={o} />
            ))}
          </div>
        )}
      </div>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.2rem',
          visibility: hover ? 'visible' : 'hidden',
        }}
      >
        {isCode && (
          <button title="Run cell (Ctrl+Enter)" onClick={onRun}>
            ▶
          </button>
        )}
        <button title="Add code cell below" onClick={() => onAddBelow('code')}>
          +
        </button>
        <button title="Add markdown cell below" onClick={() => onAddBelow('markdown')}>
          +md
        </button>
        <button title="Delete cell" onClick={onDelete}>
          ✕
        </button>
      </div>
    </div>
  );
}
