import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Extension } from '@codemirror/state';

import { useAgentContext } from '../../../agent-context';
import { usePaneParams } from '../../../panes';
import { CellEditor } from '../../../notebook/CellEditor';
import { useNotebookLsp } from '../../../notebook/useNotebookLsp';
import { listNotebooks } from '../api';
import { OutputRenderer } from '../../../notebook/OutputRenderer';
import { renderMarkdown } from '../../../notebook/markdown';
import {
  interruptKernel,
  restartKernel,
  type CellDiagnostic,
} from '../../../notebook/kernelClient';
import type { CellRunState, NotebookCell } from '../../../notebook/types';
import { useSession } from '../../../notebook/SessionStore';
import { widgetManagerFor, type WidgetManager } from '../../../notebook/widgets/WidgetManager';
import { NOTEBOOK_CHANNEL, openNotebookSession } from '../store';

const dim = { color: 'var(--text-dim)' } as const;
const EDIT_SYNC_MS = 400;

const STATE_BADGE: Record<CellRunState, string> = {
  queued: '⏳',
  running: '▶',
  done: '✓',
  error: '✗',
};

/**
 * The reactive notebook pane: cells on a kernel spawned from the managed venv.
 * Opened with params `{path}`; non-singleton so several notebooks sit side by
 * side. The kernel session is process-global backend-side — closing the pane
 * leaves it running; reopening reattaches.
 */
export function NotebookEditor() {
  const params = usePaneParams();
  const path = String(params.path ?? '');
  const store = useMemo(() => openNotebookSession(path), [path]);
  const state = useSession(store);
  const editTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  // A notebook is addressed *relative to the notebook root* everywhere in this
  // module, but the language server resolves the interpreter and the project root by
  // walking up from the file, so it needs the real path. `useNotebookLsp` turns
  // itself off for a relative one rather than resolving some other directory's
  // environment — but that would be a feature silently absent, so join it here.
  const [nbRoot, setNbRoot] = useState('');
  useEffect(() => {
    let cancelled = false;
    void listNotebooks()
      .then((res) => {
        if (!cancelled) setNbRoot(res.root);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  const absPath = useMemo(() => {
    if (!nbRoot || !path) return '';
    const sep = nbRoot.includes('\\') ? '\\' : '/';
    return `${nbRoot}${sep}${path.replace(/[\\/]/g, sep)}`;
  }, [nbRoot, path]);
  const lsp = useNotebookLsp(absPath, state.cells);

  useAgentContext(() => ({
    path,
    kernel: state.kernel,
    mode: state.mode,
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
  const widgetManager = useMemo(
    () => (sessionKey ? widgetManagerFor(NOTEBOOK_CHANNEL, sessionKey) : undefined),
    [sessionKey],
  );

  // Reattach-resync: rehydrate widget models from the `opened` snapshot so a pane
  // that reopens an already-running kernel shows live widgets, not blank ones.
  useEffect(() => {
    if (widgetManager) widgetManager.seed(state.comms);
  }, [widgetManager, state.comms]);

  const syncEdit = useCallback(
    (cellId: string, source: string) => {
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
      store.onCellsChanged({ path, cells: optimistic, metadata: {} });
    },
    [store, path],
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
      flushEdits(cellId);
      store.run(cellId);
    },
    [store, flushEdits],
  );

  const mutate = useCallback(
    (ops: Parameters<typeof store.applyLocal>[0], next: NotebookCell[]) =>
      store.applyLocal(ops, next),
    [store],
  );

  const toggleMode = useCallback(() => {
    // Optimistic + live ws set_mode (the backend persists it and rebuilds the graph).
    store.setMode(state.mode === 'reactive' ? 'classic' : 'reactive');
  }, [store, state.mode]);

  // Diagnostics grouped by cell, for per-cell markers + a header summary.
  const diagByCell = useMemo(() => {
    const m = new Map<string, CellDiagnostic[]>();
    for (const d of state.diagnostics) {
      const list = m.get(d.cellId) ?? [];
      list.push(d);
      m.set(d.cellId, list);
    }
    return m;
  }, [state.diagnostics]);

  if (!path) {
    return (
      <div style={{ padding: '1rem', ...dim }}>No notebook — open one from the Notebooks pane.</div>
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
        <span style={dim}>{path}</span>
        <button
          title="Toggle reactive / classic execution"
          onClick={toggleMode}
          style={{
            color: state.mode === 'reactive' ? 'var(--accent, #539bf5)' : 'var(--text-dim)',
          }}
        >
          {state.mode === 'reactive' ? '⚡ reactive' : '↳ classic'}
        </button>
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
        <button disabled={!sessionKey} onClick={() => store.runAll()}>
          Run all
        </button>
        <button
          disabled={!sessionKey}
          onClick={() => sessionKey && interruptKernel(NOTEBOOK_CHANNEL, sessionKey)}
        >
          Interrupt
        </button>
        <button
          disabled={!sessionKey}
          onClick={() => sessionKey && restartKernel(NOTEBOOK_CHANNEL, sessionKey)}
        >
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
      {state.mode === 'reactive' && state.diagnostics.length > 0 && (
        <div
          style={{
            padding: '0.3rem 0.5rem',
            color: 'var(--warn, #c69026)',
            fontSize: '0.72rem',
          }}
        >
          ⚠ {state.diagnostics.length} reactive issue
          {state.diagnostics.length > 1 ? 's' : ''} — cells won’t auto-run until resolved.
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
            notebookPath={path}
            lspExtensions={lsp.cellExtensions(cell)}
            runState={state.runStates[cell.id]}
            diagnostics={diagByCell.get(cell.id)}
            widgetManager={widgetManager}
            onChange={(src) => syncEdit(cell.id, src)}
            onRun={() => run(cell.id)}
            onDelete={() =>
              mutate(
                [{ op: 'delete', cellId: cell.id }],
                state.cells.filter((c) => c.id !== cell.id),
              )
            }
            onAddBelow={(type) => {
              // Assign the id up front so the new cell is immediately editable/runnable
              // (no wait to learn a server-generated id).
              const id = crypto.randomUUID();
              const temp: NotebookCell = {
                id,
                cell_type: type,
                source: '',
                outputs: [],
                execution_count: null,
              };
              const next = [...state.cells];
              next.splice(i + 1, 0, temp);
              mutate(
                [{ op: 'insert', cellId: id, afterCellId: cell.id, cellType: type, source: '' }],
                next,
              );
            }}
          />
        ))}
        {state.cells.length === 0 && sessionKey && (
          <button
            onClick={() => {
              const id = crypto.randomUUID();
              mutate(
                [{ op: 'insert', cellId: id, cellType: 'code', source: '' }],
                [{ id, cell_type: 'code', source: '', outputs: [], execution_count: null }],
              );
            }}
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
  diagnostics,
  widgetManager,
  onChange,
  onRun,
  onDelete,
  onAddBelow,
  notebookPath,
  lspExtensions,
}: {
  cell: NotebookCell;
  runState?: CellRunState;
  diagnostics?: CellDiagnostic[];
  widgetManager?: WidgetManager;
  onChange: (source: string) => void;
  onRun: () => void;
  onDelete: () => void;
  onAddBelow: (type: 'code' | 'markdown') => void;
  /** Threaded down so a cell's docs popup can ask this notebook's own kernel. */
  notebookPath?: string;
  /** This cell's slice of the notebook's language server (empty for markdown). */
  lspExtensions?: Extension[];
}) {
  const [hover, setHover] = useState(false);
  const isCode = cell.cell_type === 'code';
  // Markdown cells render to HTML; double-click (or an empty cell) shows the editor.
  const [editingMd, setEditingMd] = useState(cell.source.trim() === '');

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
        {isCode || editingMd ? (
          <CellEditor
            value={cell.source}
            language={isCode ? 'python' : 'markdown'}
            onChange={onChange}
            onRun={isCode ? onRun : () => setEditingMd(false)}
            notebookPath={notebookPath}
            extraExtensions={lspExtensions}
          />
        ) : (
          <div
            onDoubleClick={() => setEditingMd(true)}
            style={{ fontSize: '0.85rem', lineHeight: 1.5 }}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(cell.source) }}
          />
        )}
        {diagnostics && diagnostics.length > 0 && (
          <div style={{ marginTop: '0.25rem', fontSize: '0.7rem', color: 'var(--warn, #c69026)' }}>
            {diagnostics.map((d, idx) => (
              <div key={idx}>⚠ {d.message}</div>
            ))}
          </div>
        )}
        {cell.outputs.length > 0 && (
          <div
            style={{
              borderTop: '1px dashed var(--border)',
              marginTop: '0.25rem',
              paddingTop: '0.25rem',
            }}
          >
            {cell.outputs.map((o, idx) => (
              <OutputRenderer key={idx} output={o} widgetManager={widgetManager} />
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
