/**
 * The editor's **URI source model**: the buffer layer is URI-agnostic, so any
 * module shows editable text by opening a `source` URI and the editor doesn't care
 * where the bytes live. Schemes:
 *
 * - `note:<id>` — a backend-owned note (revision-tracked; saves send `base_revision`).
 * - `workspace-file:<absPath>` — a file under a workspace root (via the files module).
 * - `osfile:<path>` — desktop-only arbitrary OS file (not implemented here yet).
 *
 * See docs/modules/editor.md.
 */
import { apiGet, apiPost, apiPut } from '../../api';

export interface LoadedSource {
  content: string;
  title: string;
  /** Present for revision-tracked sources (notes); used for conflict detection. */
  revision?: number;
}

export interface SaveResult {
  revision?: number;
}

interface Note {
  id: string;
  title: string;
  content: string;
  revision: number;
}

/** Lightweight note summary (`GET /notes`), mirroring the backend `NoteMeta`. */
export interface NoteMeta {
  id: string;
  title: string;
  revision: number;
  updated_at: number;
  snippet?: string | null;
}

const NOTE = 'note:';
const FILE = 'workspace-file:';

function basename(p: string): string {
  const parts = p.split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/** A short human label for a source URI (used as the pane/tab title). */
export function sourceTitle(uri: string): string {
  if (uri.startsWith(NOTE)) return 'Note';
  if (uri.startsWith(FILE)) return basename(uri.slice(FILE.length));
  return uri;
}

/** List notes, most-recently-updated first (`GET /notes`). */
export async function listNotes(): Promise<NoteMeta[]> {
  return apiGet<NoteMeta[]>('/notes');
}

/** Create a new backend note and return its `note:<id>` source URI. */
export async function createNote(title = 'Untitled', content = ''): Promise<string> {
  const note = await apiPost<Note>('/notes', { title, content });
  return `note:${note.id}`;
}

export async function loadSource(uri: string): Promise<LoadedSource> {
  if (uri.startsWith(NOTE)) {
    const note = await apiGet<Note>(`/notes/${uri.slice(NOTE.length)}`);
    return { content: note.content, title: note.title, revision: note.revision };
  }
  if (uri.startsWith(FILE)) {
    const path = uri.slice(FILE.length);
    const file = await apiGet<{ path: string; content: string }>(
      `/files/read?path=${encodeURIComponent(path)}`,
    );
    return { content: file.content, title: basename(path) };
  }
  throw new Error(`Unsupported buffer source: ${uri}`);
}

export async function saveSource(
  uri: string,
  content: string,
  revision?: number,
): Promise<SaveResult> {
  if (uri.startsWith(NOTE)) {
    const note = await apiPut<Note>(`/notes/${uri.slice(NOTE.length)}`, {
      content,
      base_revision: revision ?? 0,
    });
    return { revision: note.revision };
  }
  if (uri.startsWith(FILE)) {
    await apiPut(`/files/write`, { path: uri.slice(FILE.length), content });
    return {};
  }
  throw new Error(`Unsupported buffer source: ${uri}`);
}
