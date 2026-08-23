/**
 * Attach a language server to a notebook pane's cells.
 *
 * Both notebook panes (the `notebook` module's and `training`'s) want the same
 * three lines of wiring, so they live here rather than twice: open a handle for the
 * notebook, re-`sync` it whenever the cell list changes, and hand each code cell a
 * stable `Extension[]` for {@link CellEditor}'s `extraExtensions`.
 *
 * The handle comes from the editor module's registered **service**, not from a deep
 * import — the kit stays domain-neutral, and this file only knows the contract
 * (`EditorService.openNotebookLsp`). A node with no Python server installed simply
 * returns extensions that stay quiet, so callers need no capability check.
 */
import { useEffect, useRef, useState } from 'react';
import type { Extension } from '@codemirror/state';

import { registry } from '../registry';
import type { EditorService, NotebookLspHandle } from '../modules/editor/service';
import type { NotebookCell } from './types';

/** No LSP: one frozen empty array, so `extraExtensions` keeps a stable identity and
 * the cell's compartment is never pointlessly reconfigured. */
const NONE: Extension[] = [];

export interface NotebookLsp {
  /** Extensions for one cell — `[]` for markdown cells and when there's no session. */
  cellExtensions(cell: NotebookCell): Extension[];
}

/**
 * @param path Absolute path to the `.ipynb`. Relative or empty disables the LSP:
 *   the interpreter and project root are resolved by walking up from the file, so a
 *   path that isn't real would silently resolve the wrong environment — which reads
 *   as "completions are wrong" rather than as a misconfiguration.
 */
export function useNotebookLsp(path: string, cells: NotebookCell[]): NotebookLsp {
  const enabled = isAbsolute(path);
  // Created in an effect rather than a `useMemo`, so every handle is paired with
  // exactly one dispose. Memoized, the StrictMode double-invoke disposed the handle
  // on the first cleanup and then went on using the dead one — which on the wire was
  // a `didClose` followed by two `didOpen`s for the same notebook.
  const [handle, setHandle] = useState<NotebookLspHandle | null>(null);
  useEffect(() => {
    if (!enabled) {
      setHandle(null);
      return;
    }
    const editor = registry.getService<EditorService>('editor');
    const opened = editor ? editor.openNotebookLsp(path) : null;
    setHandle(opened);
    return () => {
      opened?.dispose();
      setHandle(null);
    };
    // `path` is the identity of the notebook; a new one is a new document.
  }, [path, enabled]);

  // One array per cell id, kept across renders: a fresh array each time would
  // reconfigure every cell's compartment on every keystroke in any cell.
  const byCell = useRef(new Map<string, Extension[]>());

  useEffect(() => {
    handle?.sync(cells.map((c) => ({ id: c.id, cell_type: c.cell_type, source: c.source })));
    // Drop cached extensions for cells that no longer exist.
    const live = new Set(cells.map((c) => c.id));
    for (const id of byCell.current.keys()) if (!live.has(id)) byCell.current.delete(id);
  }, [handle, cells]);

  useEffect(() => {
    if (!handle) byCell.current.clear();
  }, [handle]);

  return {
    cellExtensions(cell) {
      if (!handle || cell.cell_type !== 'code') return NONE;
      const cached = byCell.current.get(cell.id);
      if (cached) return cached;
      const ext = [handle.cellExtension(cell.id)];
      byCell.current.set(cell.id, ext);
      return ext;
    },
  };
}

/** Whether a path is absolute on either platform (`/x`, `C:\x`, `\\server\share`). */
function isAbsolute(path: string): boolean {
  return /^(\/|[A-Za-z]:[\\/]|\\\\)/.test(path);
}
