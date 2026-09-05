/**
 * The editor's public, cross-module **service surface**, registered on the
 * registry under the id `editor`. Other modules (e.g. the visualizer) materialize
 * content as an editable buffer and read/write open buffers through this contract
 * instead of deep-importing the editor module's internals. See docs/modules/editor.md.
 */
import type { Extension } from '@codemirror/state';

import { registry } from '../../registry';
import { getRoots, loadRoots } from '../files/store';
import { getBuffer, listBufferUris } from './buffers';
import { getActiveBufferSource, openBuffer } from './index';
import { buildCompletion, completionKeymap } from './completion';
import { NotebookLspDoc, type LspCell } from './notebook-lsp';
import { createNote, loadSource, saveSource } from './sources';

/** Languages the editor can be told to highlight for a source with no extension. */
export type BufferLanguage = 'javascript' | 'python';

export interface OpenBufferRequest {
  content: string;
  /** Highlighting hint — notes carry no extension to infer a language from. */
  language?: BufferLanguage;
  /** Pane/tab title (notes only; files take their basename). */
  title?: string;
  /** Where the bytes live. `file` falls back to `note` when no workspace root exists. */
  prefer?: 'note' | 'file';
}

export interface EditorService {
  /** Open `content` as a new editable buffer; returns its source URI. */
  openBufferFromContent(req: OpenBufferRequest): Promise<string>;
  /** The most recently focused buffer's source URI, or null. */
  getActiveBufferSource(): string | null;
  /**
   * Current text of a buffer — the live open content if its pane is mounted, else
   * the persisted bytes via `loadSource` (so a backgrounded/unmounted tab still
   * resolves). Null if the source can't be read.
   */
  getBufferContent(uri: string): Promise<string | null>;
  /**
   * Synchronous, **live-only** read of a mounted buffer's content (no backend
   * round-trip). Null when the buffer isn't currently mounted — callers that must
   * survive an unmounted tab use the async {@link getBufferContent} instead. For
   * hot-poll loops where a backend fetch per tick would be wasteful.
   */
  peekBufferContent(uri: string): string | null;
  /**
   * The live selection in a mounted buffer. Null when the buffer isn't mounted —
   * a selection only exists in a CodeMirror view, so unlike {@link getBufferContent}
   * there is no persisted fallback. An empty `text` is a real answer (a bare
   * cursor), and callers that want "the selection, or the whole file" must check
   * `text` rather than the null.
   */
  getBufferSelection(uri: string): { text: string; from: number; to: number } | null;
  /** Replace an open buffer's content. Returns false if it isn't currently mounted. */
  setBufferContent(uri: string, content: string): boolean;
  /** Source URIs of all currently mounted buffers. */
  listBuffers(): string[];
  /**
   * Attach a language server to an open notebook. The caller (a notebook pane)
   * owns the handle: `sync` it whenever the cell list changes, hand each code
   * cell's editor its `cellExtension`, and `dispose` on unmount.
   *
   * Here rather than in the notebook kit because the kit is domain-neutral and
   * LSP is this module's; the two notebook modules reach it through this contract
   * instead of deep-importing the client.
   */
  openNotebookLsp(path: string): NotebookLspHandle;
  /**
   * The completion stack for an editor surface with **no** language server —
   * indexed symbols, curated framework imports, import-statement modules and
   * members, plus the Tab keymap that opens the popup.
   *
   * Notebook cells had neither half of this. `buildCompletion` only ran inside
   * `lspExtension`, so a cell with no server (none installed, a relative path, or
   * simply the seconds before one comes up) had no completion source at all — while
   * the identical text in a buffer had four. And no cell ever had the keymap, so
   * `from x import <Tab>`, the one gesture the import source is built around, was
   * not bound to anything.
   *
   * Exposed here rather than deep-imported because the notebook kit is
   * domain-neutral; it knows this contract and nothing else about the editor.
   */
  bareCompletion(languageId: string | null): Extension;
}

/** A notebook's language-server session, as the notebook panes see it. */
export interface NotebookLspHandle {
  /** Reconcile the server's cell array with the notebook's (cheap when unchanged). */
  sync(cells: LspCell[]): void;
  /** The CodeMirror extension giving one code cell completion, hover and diagnostics. */
  cellExtension(cellId: string): Extension;
  dispose(): void;
}

const EXT: Record<BufferLanguage, string> = { javascript: 'js', python: 'py' };

function slug(title: string): string {
  return (
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'buffer'
  );
}

async function firstRoot(): Promise<string | null> {
  if (getRoots().length === 0) await loadRoots().catch(() => undefined);
  return getRoots()[0]?.path ?? null;
}

const editorService: EditorService = {
  async openBufferFromContent({ content, language, title, prefer = 'note' }) {
    const label = title ?? 'Visualizer';

    if (prefer === 'file') {
      const root = await firstRoot();
      if (root) {
        const ext = language ? EXT[language] : 'txt';
        const sep = root.includes('\\') ? '\\' : '/';
        const path = `${root}${sep}scratch${sep}${slug(label)}-${Date.now()}.${ext}`;
        const uri = `workspace-file:${path}`;
        // `/files/write` creates parent dirs, so the scratch/ folder is made on demand.
        await saveSource(uri, content);
        // The extension drives language + LSP, so no language param needed here.
        openBuffer(uri);
        return uri;
      }
      // No workspace root (e.g. browser without files opened) → fall through to a note.
    }

    const uri = await createNote(label, content);
    openBuffer(uri, { language });
    return uri;
  },

  getActiveBufferSource,

  async getBufferContent(uri) {
    const live = getBuffer(uri);
    if (live) return live.snapshot().content;
    try {
      return (await loadSource(uri)).content;
    } catch {
      return null;
    }
  },

  peekBufferContent(uri) {
    return getBuffer(uri)?.snapshot().content ?? null;
  },

  getBufferSelection(uri) {
    const snapshot = getBuffer(uri)?.snapshot();
    if (!snapshot) return null;
    const { text, from, to } = snapshot.selection;
    return { text, from, to };
  },

  setBufferContent(uri, content) {
    const buf = getBuffer(uri);
    if (!buf) return false;
    buf.setContent(content);
    return true;
  },

  listBuffers: listBufferUris,

  bareCompletion(languageId) {
    // Mutually exclusive with the LSP stack by construction — `autocompletion()`'s
    // `override` is a replacing field, so two live instances mean one silently wins
    // and the other's sources vanish. Callers pick one or the other, never both.
    return [buildCompletion({ languageId }), completionKeymap];
  },

  openNotebookLsp(path) {
    const doc = new NotebookLspDoc(path);
    // Detached: the pane gets a usable handle immediately and `sync` queues cells
    // until the environment resolves and the session comes up.
    void doc.start();
    return doc;
  },
};

/** Register the editor service on the shared registry (called once at module load). */
export function registerEditorService(): void {
  registry.provideService<EditorService>('editor', editorService);
}
