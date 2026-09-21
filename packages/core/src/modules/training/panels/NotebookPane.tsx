import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import type { Extension } from '@codemirror/state';

import { getProject, type NotebookCell } from '../api';
import {
  interruptKernel,
  restartKernel,
  runAll,
  runCell,
  type CellOp,
  type CellRunState,
} from '../client';
import { openSession, useSession } from '../store';
import { forgetLastProject, lastProjectId } from '../last-project';
import { OutputRenderer } from '../outputs/OutputRenderer';
import { CellEditor } from './CellEditor';
import { ProjectsPane } from './ProjectsPane';
import { advanceFrom } from '../../../notebook/advance';
import { renderMarkdown } from '../../../notebook/markdown';
import { useNotebookLsp } from '../../../notebook/useNotebookLsp';
import { PaneInstanceContext, useAgentContext } from '../../../agent-context';
import { usePaneUiState } from '../../../layout/use-pane-ui-state';
import { usePaneParams } from '../../../panes';
import { registry } from '../../../registry';
import '../../../notebook/notebook.css';

const dim = { color: 'var(--text-dim)' } as const;

const STATE_BADGE: Record<CellRunState, string> = {
  queued: '⏳',
  running: '▶',
  done: '✓',
  error: '✗',
};

const EDIT_SYNC_MS = 400;

/**
 * The projects list, shown in place of a notebook.
 *
 * Two branches reach it — no project at all, and a remembered project that no
 * longer exists — and they must show the same thing, so it is one component rather
 * than a block of JSX copied into the second branch. Choosing a project calls
 * `openTrainingNotebook`, whose `canReuse` retargets *this* pane instance in place,
 * so the pane the user is looking at becomes the notebook instead of leaving an
 * empty one behind.
 */
function ProjectPicker() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          padding: '0.5rem 0.75rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.7rem',
          fontWeight: 700,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          ...dim,
        }}
      >
        Choose a project
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <ProjectsPane />
      </div>
    </div>
  );
}

/**
 * The native notebook pane: cells on a per-project Jupyter kernel. Opened with
 * params `{projectId, notebook?}`; non-singleton so several notebooks can sit
 * side by side. The kernel session is process-global backend-side — closing the
 * pane leaves training running; reopening reattaches.
 */
export function NotebookPane() {
  const params = usePaneParams();
  const instanceId = useContext(PaneInstanceContext);
  // Opened without params — from a preset's seed, the start menu, or the palette —
  // this falls back to the project you were last in. That is what lets the
  // `training` and `ai-research` presets seed this pane at all: they used to seed
  // an empty area, because a preset's `tabs` carry no params and a params-less
  // notebook had nothing to show. Resolved once per mount rather than watched: the
  // remembered id changing under an open pane should not swap the notebook out from
  // under someone.
  const [fallbackId] = useState(() => (params.projectId ? null : lastProjectId()));
  const projectId = String(params.projectId ?? fallbackId ?? '');
  const viaFallback = !params.projectId && !!fallbackId;
  const notebookPath = String(params.notebook ?? 'main.ipynb');
  const store = useMemo(() => openSession(projectId, notebookPath), [projectId, notebookPath]);
  const state = useSession(store);
  const editTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  // The pane addresses notebooks by a path relative to the project, but the language
  // server resolves the interpreter and the project root by walking *up* from the
  // file — so it needs the real one. `.venv` lives at the project root, which is the
  // whole point here: a fine-tuning notebook must complete against the torch/trl it
  // will actually run on, not against the dashboard's own environment.
  const [projectRoot, setProjectRoot] = useState('');
  useEffect(() => {
    let cancelled = false;
    if (!projectId) return;
    void getProject(projectId)
      .then((p) => {
        if (!cancelled) setProjectRoot(p.root);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [projectId]);
  const absNotebookPath = useMemo(() => {
    if (!projectRoot) return '';
    const sep = projectRoot.includes('\\') ? '\\' : '/';
    return `${projectRoot}${sep}${notebookPath.replace(/[\\/]/g, sep)}`;
  }, [projectRoot, notebookPath]);
  const lsp = useNotebookLsp(absNotebookPath, state.cells);

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

  /**
   * Which markdown cell is open as an editor, if any. Pane-scoped rather than
   * component state so glancing at another tab does not throw away a half-typed
   * heading — the same treatment the notebook module's editor gives it.
   */
  const [editingMd, setEditingMd] = usePaneUiState<string | null>('editingMd', null);
  /**
   * A request to put the caret somewhere, by **position**. Not by cell id: a cell
   * inserted at the end carries a `tmp-…` id until `cells_changed` answers with
   * the real one, and that swap remounts the row — a request keyed by the temp id
   * would be asking for a cell that no longer exists by the time it mounts.
   */
  const [focusReq, setFocusReq] = useState<{ index: number; n: number } | null>(null);
  const focusAt = useCallback((index: number) => {
    // Monotonic, because focus is an event: the same index can be asked for twice
    // running (Shift+Enter at the bottom, then again in the cell it appended).
    setFocusReq((prev) => ({ index, n: (prev?.n ?? 0) + 1 }));
  }, []);

  const run = useCallback(
    (cellId: string) => {
      const cell = store.snapshot().cells.find((c) => c.id === cellId);
      if (!cell) return;
      flushEdits(cellId);
      // Markdown is *rendered*, not run. Sending it to the kernel is what produced
      // `no code cell <id>` in the error banner on every Shift+Enter in a prose
      // cell: the backend rejects a non-code cell, and this pane never rendered
      // markdown at all, so there was no other thing the key could have meant.
      if (cell.cell_type !== 'code') {
        if (editingMd === cellId) setEditingMd(null);
        return;
      }
      if (!sessionKey) return;
      runCell(sessionKey, cellId);
    },
    [sessionKey, flushEdits, store, editingMd, setEditingMd],
  );

  const mutate = useCallback(
    (ops: CellOp[], next: NotebookCell[]) => store.applyLocal(ops, next),
    [store],
  );

  /** Shift+Enter: run (or render) this cell, then land in the next one. */
  const runNext = useCallback(
    (cellId: string, index: number) => {
      run(cellId);
      const cells = store.snapshot().cells;
      const next = advanceFrom(cells, index);
      if (next.kind === 'focus') {
        const target = cells[next.index];
        // Landing on a *rendered* markdown cell would end the walk: there is no
        // editor there to hold the caret, so the next Shift+Enter would go
        // nowhere. Opening it keeps the contract simple — the cell you are on is
        // the one you can edit, and Shift+Enter commits it and moves on. This is
        // not Jupyter's command mode, which would need a selection model the
        // notebook does not have.
        if (target?.cell_type === 'markdown') setEditingMd(target.id);
        focusAt(next.index);
        return;
      }
      // Past the last cell: append one and write in it, as Jupyter does.
      const temp: NotebookCell = {
        id: `tmp-${Date.now()}`,
        cell_type: 'code',
        source: '',
        outputs: [],
        execution_count: null,
      };
      mutate(
        [
          {
            op: 'insert',
            ...(next.afterCellId ? { afterCellId: next.afterCellId } : {}),
            cellType: 'code',
            source: '',
          },
        ],
        [...cells, temp],
      );
      focusAt(next.index);
    },
    [run, store, mutate, focusAt, setEditingMd],
  );

  // No project at all, and none remembered. It used to say so and point at the
  // projects *pane* — which pane consolidation turned into an Explorer section, so
  // the instruction named something the user could not open.
  if (!projectId) return <ProjectPicker />;

  // The *remembered* project is gone. Forget it and show the picker. The dead-pane
  // message below is right for a pane the user explicitly opened against that
  // project and wrong here, where nobody asked for this project and the value is not
  // one they can see or clear — without this the workspace would greet them with the
  // same dead project on every open, forever.
  if (state.errorCode === 'unknown_project' && viaFallback) {
    forgetLastProject();
    return <ProjectPicker />;
  }

  // The project this pane was persisted against no longer exists (deleted, or a
  // partial dir with no project.json). Offer to close the dead pane — closing it
  // drops it from the saved layout so it won't reattach-and-error on next load.

  if (state.errorCode === 'unknown_project') {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.6rem',
          alignItems: 'flex-start',
          padding: '1rem',
          fontSize: '0.85rem',
        }}
      >
        <div>
          <strong>{projectId}</strong> no longer exists.
        </div>
        <div style={dim}>This training project was deleted or is missing its data.</div>
        <button
          onClick={() => instanceId && registry.layoutController?.closePane(instanceId)}
          disabled={!instanceId}
        >
          Close pane
        </button>
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
        {/* With the socket down every status here is a *last known* one, so the
            badge says so rather than repeating it. The failure this replaces:
            a backend restart takes its kernel with it, no event can arrive to
            say so, and the pane held "● busy" over a column of queued cells
            indefinitely — which looks exactly like a cell that is taking a
            while, for as long as you are willing to wait. */}
        <span
          title={
            state.connected
              ? 'Kernel status'
              : 'The connection to the backend dropped — this is the last status it sent, not a current one. Reconnecting.'
          }
          style={{
            color: !state.connected
              ? 'var(--warn, #d29922)'
              : state.kernel === 'idle'
                ? 'var(--ok, #57ab5a)'
                : state.kernel === 'dead'
                  ? 'var(--danger, #e5534b)'
                  : 'var(--text-dim)',
          }}
        >
          {state.connected ? `● ${state.kernel}` : '◌ reconnecting…'}
        </span>
        <button
          disabled={!sessionKey || !state.connected}
          onClick={() => sessionKey && runAll(sessionKey)}
        >
          Run all
        </button>
        <button
          disabled={!sessionKey || !state.connected}
          onClick={() => sessionKey && interruptKernel(sessionKey)}
        >
          Interrupt
        </button>
        <button
          disabled={!sessionKey || !state.connected}
          onClick={() => sessionKey && restartKernel(sessionKey)}
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
      <div style={{ flex: 1, overflow: 'auto', padding: '0.5rem' }}>
        {!sessionKey && !state.error && (
          <div style={{ fontSize: '0.8rem', ...dim }}>Starting kernel…</div>
        )}
        {state.cells.map((cell, i) => (
          <Cell
            key={cell.id}
            cell={cell}
            lspExtensions={lsp.cellExtensions(cell)}
            runState={state.runStates[cell.id]}
            editingMd={editingMd === cell.id}
            onEditingMd={(on) => setEditingMd(on ? cell.id : null)}
            focusToken={focusReq?.index === i ? focusReq.n : 0}
            onChange={(src) => syncEdit(cell.id, src)}
            onRun={() => run(cell.id)}
            onRunNext={() => runNext(cell.id, i)}
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
  editingMd,
  focusToken,
  onChange,
  onRun,
  onRunNext,
  onEditingMd,
  onDelete,
  onAddBelow,
  lspExtensions,
}: {
  cell: NotebookCell;
  runState?: CellRunState;
  /** This cell's slice of the notebook's language server (empty for markdown). */
  lspExtensions?: Extension[];
  /** Markdown only: show the editor rather than the rendered prose. */
  editingMd: boolean;
  focusToken: number;
  onChange: (source: string) => void;
  onRun: () => void;
  onRunNext: () => void;
  onEditingMd: (editing: boolean) => void;
  onDelete: () => void;
  onAddBelow: (type: 'code' | 'markdown') => void;
}) {
  const [hover, setHover] = useState(false);
  const isCode = cell.cell_type === 'code';
  // An empty markdown cell opens straight into the editor: rendering nothing
  // would leave a row you have to know to double-click.
  const showEditor = isCode || editingMd || cell.source.trim() === '';
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
        {showEditor ? (
          <CellEditor
            value={cell.source}
            language={isCode ? 'python' : 'markdown'}
            onChange={onChange}
            // For markdown, "run" means render — the editor closes and the prose
            // takes its place. Both keys go through the pane so the two meanings
            // live in one function rather than being re-decided here.
            onRun={onRun}
            onRunNext={onRunNext}
            focusToken={focusToken}
            extraExtensions={lspExtensions}
          />
        ) : (
          <div
            className="nb-markdown"
            title="Double-click to edit"
            onDoubleClick={() => onEditingMd(true)}
            // Same renderer the notebook module uses; it escapes its input before
            // doing anything else (see notebook/markdown.ts).
            dangerouslySetInnerHTML={{ __html: renderMarkdown(cell.source) }}
          />
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
        {isCode ? (
          <button title="Run cell (Ctrl+Enter) — Shift+Enter runs and moves on" onClick={onRun}>
            ▶
          </button>
        ) : showEditor ? (
          <button title="Render (Ctrl+Enter) — Shift+Enter renders and moves on" onClick={onRun}>
            ▶
          </button>
        ) : (
          <button title="Edit this markdown (or double-click it)" onClick={() => onEditingMd(true)}>
            ✎
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
