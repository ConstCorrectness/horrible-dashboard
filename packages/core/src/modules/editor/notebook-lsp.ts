/**
 * Language-server support for **notebook cells** — the half of the editor's LSP
 * that the notebook panes use (both the `notebook` module and `training`'s).
 *
 * A notebook is one document made of cells, not N little files: `import torch` in
 * cell 1 has to be what makes `torch.` complete in cell 7. LSP 3.17's
 * `notebookDocument/*` notifications say exactly that, and basedpyright implements
 * them — so this file is a *coordinator*, not a second LSP client. One
 * `NotebookLspDoc` per open notebook owns the session and the cell list; each cell's
 * CodeMirror view gets an ordinary {@link lspExtension} with a binding that routes
 * its sync through the notebook instead of through `textDocument/*`.
 *
 * Everything here was measured against basedpyright 1.39.9 rather than read off the
 * spec, because all three traps below fail **silently** — the server stays up and
 * answers, it just answers about a document that isn't yours:
 *
 * - A cell document must use the **`vscode-notebook-cell:` scheme**. Under `file:`
 *   with a `#cell0` fragment the server accepts the notebook and then resolves
 *   nothing: no diagnostics, empty completions, no error. (The fragment is free-form,
 *   so we use the nbformat cell id, which is stable across reordering.)
 * - A cell's edits must ride **`notebookDocument/didChange`**. A plain
 *   `textDocument/didChange` aimed at a cell URI is accepted and discarded, leaving
 *   the server analyzing the text the cell was opened with.
 * - Only **code** cells belong to the document. The server's own selector asks for
 *   `cells: [{language: python}]`, and a markdown cell in the array shifts every
 *   later cell's index, so structure edits would land in the wrong place.
 *
 * See docs/modules/editor.mdx.
 */
import type { Extension } from '@codemirror/state';

import {
  acquireSession,
  dirOf,
  lspExtension,
  pathToUri,
  releaseSession,
  type DocumentBinding,
  type LspDiagnostic,
  type LspSession,
  type NotebookCellDoc,
} from './lsp';
import { fetchPythonEnv } from './pythonEnv';

/** The cell shape the panes already hold (a subset of the kit's `NotebookCell`). */
export interface LspCell {
  id: string;
  cell_type: 'code' | 'markdown';
  source: string;
}

/** What a cell's editor registers so the coordinator can read its live text and
 * deliver its diagnostics. Absent until the cell's view mounts. */
interface LiveCell {
  text: () => string;
  onDiagnostics: (raw: LspDiagnostic[]) => void;
}

/**
 * One open notebook's language-server state. Created by the pane, told the cell
 * list whenever it changes, and disposed when the pane unmounts.
 */
export class NotebookLspDoc {
  private session: LspSession | null = null;
  private disposed = false;
  /** Whether `notebookDocument/didOpen` has gone out. Exactly one per notebook. */
  private opened = false;
  /** Code-cell ids in notebook order — the array the server's cell indices index. */
  private order: string[] = [];
  /** Last text we sent for each cell, so a re-sync doesn't re-send unchanged cells. */
  private sent = new Map<string, string>();
  private live = new Map<string, LiveCell>();
  private version = 1;
  private cellVersions = new Map<string, number>();
  /** Cells the pane described before the session came up. */
  private pending: LspCell[] | null = null;

  readonly notebookUri: string;

  constructor(readonly path: string) {
    this.notebookUri = pathToUri(path);
  }

  /** The cell URI for a cell id. Scheme is load-bearing — see the module comment. */
  cellUri(cellId: string): string {
    return `vscode-notebook-cell://${this.notebookUri.replace(/^file:\/\//, '')}#${cellId}`;
  }

  /** Resolve the environment and join the shared session. Opens the notebook only if
   * the pane has already described its cells — otherwise the first `sync` does it.
   *
   * Splitting it this way is not tidiness: `fetchPythonEnv` is cached per directory,
   * so on a second open it resolves in a microtask — before React has run the effect
   * that calls `sync`. Opening here regardless sent `didOpen` with an empty cell
   * array, and a notebook the server believes has no cells resolves nothing in any
   * of them. The pane's cells are the trigger; the session is just a precondition. */
  async start(): Promise<void> {
    const resolved = await fetchPythonEnv(dirOf(this.path));
    if (this.disposed) return;
    const interpreter = resolved.interpreter || undefined;
    const root = resolved.root || dirOf(this.path);
    this.session = acquireSession(
      `python::${root}::${interpreter ?? ''}`,
      'python',
      root,
      interpreter,
    );
    if (this.pending) {
      const cells = this.pending;
      this.pending = null;
      this.open(cells.filter((c) => c.cell_type === 'code'));
    }
  }

  /** Send the one `notebookDocument/didOpen` for this notebook. */
  private open(code: LspCell[]): void {
    if (!this.session || this.opened) return;
    this.opened = true;
    this.order = code.map((c) => c.id);
    for (const cell of code) this.sent.set(cell.id, cell.source);
    this.session.openNotebook(
      this.notebookUri,
      code.map((c) => this.cellDoc(c.id, c.source)),
    );
  }

  private cellDoc(cellId: string, fallback: string): NotebookCellDoc {
    return {
      uri: this.cellUri(cellId),
      // The mounted editor is the truth when there is one; the notebook's stored
      // source covers a cell whose view hasn't mounted (or never will — a pane can
      // virtualize, and the server still needs the cell to chain scopes correctly).
      text: () => this.live.get(cellId)?.text() ?? fallback,
      doc: { onDiagnostics: (raw) => this.live.get(cellId)?.onDiagnostics(raw) },
    };
  }

  /**
   * Reconcile the server's cell array with the notebook's. Called by the pane on
   * every cell-list change; a no-op when nothing structural moved.
   *
   * Only insertions and deletions are diffed, as one splice per contiguous run.
   * A *move* shows up as a delete plus an insert, which is correct if verbose —
   * and cell moves are rare next to typing.
   */
  sync(cells: LspCell[]): void {
    const code = cells.filter((c) => c.cell_type === 'code');
    if (!this.session) {
      this.pending = cells;
      return;
    }
    if (!this.opened) {
      this.open(code);
      return;
    }
    const next = code.map((c) => c.id);
    const sourceById = new Map(code.map((c) => [c.id, c.source]));
    for (const [cellId, source] of sourceById) {
      // A cell the pane changed while its view was unmounted (an agent edit, a
      // `cells_changed` broadcast) has no editor to push the change for it.
      if (!this.live.has(cellId) && this.sent.has(cellId) && this.sent.get(cellId) !== source) {
        this.cellChanged(cellId, source);
      }
    }
    if (next.length === this.order.length && next.every((id, i) => id === this.order[i])) return;

    // Walk both orders once, emitting a splice per divergence. Indices are into the
    // *current* server array, so applying each change as we go keeps them valid.
    const current = [...this.order];
    let i = 0;
    while (i < current.length || i < next.length) {
      if (current[i] === next[i]) {
        i++;
        continue;
      }
      const removed: string[] = [];
      while (i < current.length && !next.includes(current[i])) removed.push(current[i++]);
      if (removed.length) {
        const start = i - removed.length;
        current.splice(start, removed.length);
        i = start;
        this.session.notebookStructureChanged(this.notebookUri, ++this.version, {
          start,
          deleteCount: removed.length,
          opened: [],
          closed: removed.map((id) => this.cellUri(id)),
        });
        for (const id of removed) {
          this.sent.delete(id);
          this.cellVersions.delete(id);
        }
        continue;
      }
      const added: string[] = [];
      while (i < next.length && !current.includes(next[i])) added.push(next[i++]);
      if (added.length) {
        const start = i - added.length;
        current.splice(start, 0, ...added);
        this.session.notebookStructureChanged(this.notebookUri, ++this.version, {
          start,
          deleteCount: 0,
          opened: added.map((id) => this.cellDoc(id, sourceById.get(id) ?? '')),
          closed: [],
        });
        for (const id of added) this.sent.set(id, sourceById.get(id) ?? '');
        continue;
      }
      // Neither pure insert nor pure delete at this index — a move. Skip past it;
      // the next pass over a settled list resolves it as a delete+insert.
      i++;
    }
    this.order = current;
  }

  /** Push one cell's new text. */
  private cellChanged(cellId: string, text: string): void {
    if (!this.session) return;
    this.sent.set(cellId, text);
    const cellVersion = (this.cellVersions.get(cellId) ?? 1) + 1;
    this.cellVersions.set(cellId, cellVersion);
    this.session.notebookCellChanged(
      this.notebookUri,
      ++this.version,
      this.cellUri(cellId),
      cellVersion,
      text,
    );
  }

  /**
   * The CodeMirror extension for one code cell's editor. Completion, hover and
   * diagnostics come from the same client a file buffer uses; only the sync differs.
   *
   * Deliberately no F12/F2: a definition or rename can land in *another* cell, and
   * jumping there means mapping a cell URI back to a mounted editor the coordinator
   * does not own. Offering a key that silently does nothing across cells is worse
   * than not offering it, so cross-cell navigation waits for the pane to expose a
   * "reveal cell" callback.
   */
  cellExtension(cellId: string): Extension {
    return lspExtension({
      path: this.path,
      languageId: 'python',
      root: dirOf(this.path),
      bufferUri: `notebook-cell:${this.path}#${cellId}`,
      binding: this.binding(cellId),
      // A notebook is the place people reach for a framework they haven't imported
      // yet, so the curated import source earns its keep here more than anywhere.
      frameworkImports: true,
    });
  }

  private binding(cellId: string): DocumentBinding {
    return {
      uri: this.cellUri(cellId),
      open: async (getText, onDiagnostics) => {
        this.live.set(cellId, { text: getText, onDiagnostics });
        // The pane starts the doc; a cell mounting before that resolves just waits.
        while (!this.session && !this.disposed) {
          await new Promise((r) => window.setTimeout(r, 50));
        }
        if (this.disposed) return null;
        // A cell whose view mounts after the notebook opened is already in the
        // server's array — `sync` put it there from the stored source.
        return this.session;
      },
      change: (_version, text) => this.cellChanged(cellId, text),
      close: () => {
        // The cell document stays open on the server: the *notebook* owns it, and a
        // cell scrolled out of view still has to chain scopes for the ones below it.
        // Only the live-text/diagnostics hookup goes away.
        this.live.delete(cellId);
      },
    };
  }

  dispose(): void {
    this.disposed = true;
    this.live.clear();
    if (!this.session || !this.opened) {
      // Acquired but never opened (torn down during the environment fetch): still
      // owes the pool a release, or the server stays up with no documents.
      if (this.session) releaseSession(this.session);
      this.session = null;
      return;
    }
    this.session.closeNotebook(
      this.notebookUri,
      this.order.map((id) => this.cellUri(id)),
    );
    releaseSession(this.session);
    this.session = null;
  }
}
