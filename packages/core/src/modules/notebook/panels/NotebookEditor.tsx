import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Extension } from '@codemirror/state';

import { useAgentContext } from '../../../agent-context';
import { usePaneScroll, usePaneUiState } from '../../../layout/use-pane-ui-state';
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
import {
  CloseIcon,
  PlayIcon,
  PlusIcon,
  PlusTextIcon,
  RunStateIcon,
  WarnIcon,
} from '../../../notebook/CellIcons';
import { NOTEBOOK_CHANNEL, openNotebookSession } from '../store';
import { NotebookBrowser } from './NotebookBrowser';
import '../../../notebook/notebook.css';

const dim = { color: 'var(--text-dim)' } as const;
const EDIT_SYNC_MS = 400;

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
  // Where the pane was scrolled to, and which markdown cell was open for editing.
  // Both live in `pane-lifetime`'s view-state bag, so a workspace switch (which
  // unmounts every pane) does not scroll the notebook back to the top or close a
  // cell mid-edit. See `use-pane-ui-state.ts`.
  const scrollRef = usePaneScroll<HTMLDivElement>();
  const [editingMd, setEditingMd] = usePaneUiState<string | null>('editingMd', null);
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

  // Flush on unmount rather than only clearing. A pending edit is at most
  // `EDIT_SYNC_MS` old, and clearing the timer without running it drops it from the
  // backend document — so a change typed a third of a second before switching tabs
  // silently never happened. The optimistic copy in the store made this invisible
  // until the notebook was reloaded from disk.
  const flushAllRef = useRef<() => void>(() => {});
  useEffect(
    () => () => {
      flushAllRef.current();
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

  flushAllRef.current = () => {
    for (const cellId of [...editTimers.current.keys()]) flushEdits(cellId);
  };

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

  // No notebook bound — opened from the start menu, the palette, or an empty
  // area. This used to point at the Notebooks *pane*, which consolidation made an
  // Explorer section and therefore not something the user could open. Render the
  // browser here instead: picking a notebook calls `openNotebook`, whose
  // `canReuse` retargets this very instance, so the pane becomes the notebook.
  if (!path) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div
          style={{
            padding: '0.5rem 0.75rem',
            borderBottom: '1px solid var(--border)',
            fontSize: 'var(--fs-label)',
            fontWeight: 700,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            ...dim,
          }}
        >
          Choose a notebook
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <NotebookBrowser />
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="nb-header">
        <span className="nb-header-path" title={path}>
          {path}
        </span>
        <button
          title="Toggle reactive / classic execution"
          className={state.mode === 'reactive' ? 'is-on' : undefined}
          onClick={toggleMode}
        >
          {state.mode === 'reactive' ? 'reactive' : 'classic'}
        </button>
        <span style={{ flex: 1 }} />
        <span className={`nb-kernel nb-kernel--${state.kernel}`} title="Kernel status">
          {state.kernel}
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
      {state.error && <div className="nb-banner nb-banner--error">{state.error}</div>}
      {state.mode === 'reactive' && state.diagnostics.length > 0 && (
        <div className="nb-banner nb-banner--warn">
          {state.diagnostics.length} reactive issue
          {state.diagnostics.length > 1 ? 's' : ''} — cells won’t auto-run until resolved.
        </div>
      )}
      <div className="nb-scroll" ref={scrollRef}>
        {!sessionKey && !state.error && (
          <div style={{ fontSize: 'var(--fs-body)', ...dim }}>Starting kernel…</div>
        )}
        {state.cells.map((cell, i) => (
          <Cell
            key={cell.id}
            index={i}
            cell={cell}
            notebookPath={path}
            editing={editingMd === cell.id}
            onEditing={(on) => setEditingMd(on ? cell.id : null)}
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
  index,
  runState,
  diagnostics,
  widgetManager,
  editing,
  onEditing,
  onChange,
  onRun,
  onDelete,
  onAddBelow,
  notebookPath,
  lspExtensions,
}: {
  cell: NotebookCell;
  /** Position in the notebook, for the capped entrance stagger. */
  index: number;
  runState?: CellRunState;
  diagnostics?: CellDiagnostic[];
  widgetManager?: WidgetManager;
  /** Whether this markdown cell is open in the editor. Owned by the pane so it
   *  survives an unmount — a cell you were writing must not snap back to rendered
   *  HTML because you glanced at another tab. */
  editing: boolean;
  onEditing: (editing: boolean) => void;
  onChange: (source: string) => void;
  onRun: () => void;
  onDelete: () => void;
  onAddBelow: (type: 'code' | 'markdown') => void;
  /** Threaded down so a cell's docs popup can ask this notebook's own kernel. */
  notebookPath?: string;
  /** This cell's slice of the notebook's language server (empty for markdown). */
  lspExtensions?: Extension[];
}) {
  const isCode = cell.cell_type === 'code';
  // An empty markdown cell has nothing to render, so it opens straight into the
  // editor rather than showing a blank box the user has to guess is double-clickable.
  const editingMd = editing || cell.source.trim() === '';

  return (
    <div
      className={[
        'nb-cell',
        isCode ? 'nb-cell--code' : 'nb-cell--markdown',
        runState ? `nb-cell--${runState}` : '',
      ]
        .filter(Boolean)
        .join(' ')}
      // Capped at 12 steps: a long notebook must not spend two seconds arriving.
      style={{ ['--nb-i' as string]: Math.min(index, 12) }}
    >
      <div className="nb-gutter">
        {isCode ? (
          <>
            <span>[{cell.execution_count ?? ' '}]</span>
            {runState ? <RunStateIcon state={runState} /> : null}
          </>
        ) : (
          <span className="nb-gutter-kind">md</span>
        )}
      </div>
      <div className="nb-body">
        {isCode || editingMd ? (
          <CellEditor
            value={cell.source}
            language={isCode ? 'python' : 'markdown'}
            onChange={onChange}
            onRun={isCode ? onRun : () => onEditing(false)}
            notebookPath={notebookPath}
            extraExtensions={lspExtensions}
          />
        ) : (
          <div
            className="nb-markdown"
            onDoubleClick={() => onEditing(true)}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(cell.source) }}
          />
        )}
        {diagnostics && diagnostics.length > 0 && (
          <div className="nb-diagnostics">
            {diagnostics.map((d, idx) => (
              <div key={idx} style={{ display: 'flex', gap: '0.3rem', alignItems: 'flex-start' }}>
                <WarnIcon />
                <span>{d.message}</span>
              </div>
            ))}
          </div>
        )}
        {cell.outputs.length > 0 && (
          <div className="nb-outputs">
            {cell.outputs.map((o, idx) => (
              <OutputRenderer key={idx} output={o} widgetManager={widgetManager} />
            ))}
          </div>
        )}
      </div>
      <div className="nb-actions">
        {isCode && (
          <button type="button" title="Run cell (Ctrl+Enter)" onClick={onRun}>
            <PlayIcon />
          </button>
        )}
        <button type="button" title="Add code cell below" onClick={() => onAddBelow('code')}>
          <PlusIcon />
        </button>
        <button
          type="button"
          title="Add markdown cell below"
          onClick={() => onAddBelow('markdown')}
        >
          <PlusTextIcon />
        </button>
        <button type="button" className="is-danger" title="Delete cell" onClick={onDelete}>
          <CloseIcon />
        </button>
      </div>
    </div>
  );
}
