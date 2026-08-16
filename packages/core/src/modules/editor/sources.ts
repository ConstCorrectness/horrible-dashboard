/**
 * The editor's **URI source model**: the buffer layer is URI-agnostic, so any
 * module shows editable text by opening a `source` URI and the editor doesn't care
 * where the bytes live. Schemes:
 *
 * - `note:<id>` — a backend-owned note (revision-tracked; saves send `base_revision`).
 * - `workspace-file:<absPath>` — a file under a workspace root (via the files module).
 * - `gdrive:/<fileId>` — a Google Drive file, read-only (via the files module's
 *   virtual-root provider; Docs are exported to text and PDFs extracted).
 * - `github:<owner>/<repo>@<ref>/<path>` — a file in a GitHub repo, read-only.
 * - `osfile:<path>` — desktop-only arbitrary OS file (not implemented here yet).
 *
 * Sources that can't be written back set `readOnly` on load; the buffer then renders
 * without a Save button and CodeMirror refuses edits, so "read-only" is enforced in the
 * editor rather than relying on every caller to remember. `saveSource` throws for them
 * as a backstop.
 *
 * See docs/modules/editor.mdx.
 */
import { apiGet, apiPost, apiPut } from '../../api';

export interface LoadedSource {
  content: string;
  title: string;
  /** Present for revision-tracked sources (notes); used for conflict detection. */
  revision?: number;
  /** The source can be read but not written back (Drive files, GitHub blobs). */
  readOnly?: boolean;
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
const GDRIVE = 'gdrive:';
const GITHUB = 'github:';

function basename(p: string): string {
  const parts = p.split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/** Join a directory and a name with whichever separator the directory already uses,
 * so a Windows root doesn't grow a mixed `C:\Users\x/notes.md`. */
export function joinPath(dir: string, name: string): string {
  const sep = dir.includes('\\') ? '\\' : '/';
  return `${dir.replace(/[\\/]+$/, '')}${sep}${name}`;
}

/** The directory portion of a path (empty when there is none). */
export function dirname(p: string): string {
  const cut = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'));
  return cut > 0 ? p.slice(0, cut) : '';
}

/** Split `github:<owner>/<repo>@<ref>/<path>` into its parts. */
export function parseGithubUri(uri: string): {
  owner: string;
  repo: string;
  ref: string;
  path: string;
} | null {
  const match = /^github:([^/]+)\/([^@]+)@([^/]+)\/(.+)$/.exec(uri);
  if (!match) return null;
  return { owner: match[1], repo: match[2], ref: match[3], path: match[4] };
}

/** A short human label for a source URI (used as the pane/tab title). */
export function sourceTitle(uri: string): string {
  if (uri.startsWith(NOTE)) return 'Note';
  if (uri.startsWith(FILE)) return basename(uri.slice(FILE.length));
  // A Drive path carries a file id, not a name — the real title arrives with the
  // content, so show something neutral rather than the raw id.
  if (uri.startsWith(GDRIVE)) return 'Drive file';
  if (uri.startsWith(GITHUB)) return basename(parseGithubUri(uri)?.path ?? uri);
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
  if (uri.startsWith(GDRIVE)) {
    // Drive rides the files API's virtual-root provider, so the URI *is* the path.
    const file = await apiGet<{ path: string; content: string; name?: string }>(
      `/files/read?path=${encodeURIComponent(uri)}`,
    );
    return { content: file.content, title: file.name || 'Drive file', readOnly: true };
  }
  if (uri.startsWith(GITHUB)) {
    const parts = parseGithubUri(uri);
    if (!parts) throw new Error(`Malformed GitHub source: ${uri}`);
    const { owner, repo, ref, path } = parts;
    const file = await apiGet<{ content: string }>(
      `/connectors/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/file` +
        `?path=${encodeURIComponent(path)}&ref=${encodeURIComponent(ref)}`,
    );
    return { content: file.content, title: basename(path), readOnly: true };
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
  // A backstop, not the primary guard: the buffer hides Save and CodeMirror refuses
  // edits for these. Reaching here means a caller went around the editor.
  if (uri.startsWith(GDRIVE)) throw new Error('Google Drive files are read-only here.');
  if (uri.startsWith(GITHUB)) throw new Error('GitHub files are read-only here.');
  throw new Error(`Unsupported buffer source: ${uri}`);
}
