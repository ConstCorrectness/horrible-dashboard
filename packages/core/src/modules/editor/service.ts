/**
 * The editor's public, cross-module **service surface**, registered on the
 * registry under the id `editor`. Other modules (e.g. the visualizer) materialize
 * content as an editable buffer and read/write open buffers through this contract
 * instead of deep-importing the editor module's internals. See docs/modules/editor.md.
 */
import { registry } from '../../registry';
import { getRoots, loadRoots } from '../files/store';
import { getBuffer, listBufferUris } from './buffers';
import { getActiveBufferSource, openBuffer } from './index';
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
};

/** Register the editor service on the shared registry (called once at module load). */
export function registerEditorService(): void {
  registry.provideService<EditorService>('editor', editorService);
}
